"""Cohort analysis on hand-built tables (no ClickHouse): each property the Gate 0 design promises."""

from __future__ import annotations

import json
import random

from anomalyos.cohorts.analysis import (CohortRow, analyze, benjamini_hochberg, bh_qvalues, bundle, decompose_rate,
                                        estimate_impact, locus_row)
from anomalyos.cohorts.config import CohortConfig

CFG = CohortConfig()
CAND = {"anomaly_id": "anom_test", "metric": "authorization_rate", "metric_version": 1, "direction": "down", "scope": (),
        "window_start": 0, "window_end": 3600, "observed": 0.8, "expected": 0.9, "evidence_ids": ("evd_a",), "grain": "1h"}


def row(dims, rb, nb, rd, nd, days=7):
    """Cohort with rate rb on nb attempts before (spread over `days` reference days) and rd on nd during."""
    per = [(round(rb * nb / days), round(nb / days))] * days
    return CohortRow(tuple(dims), sum(k for k, _ in per), sum(n for _, n in per), round(rd * nd), nd, tuple(per))


def test_decomposition_is_exact():
    rng = random.Random(1)
    for _ in range(50):
        rows = [CohortRow((("c", str(i)),), rng.randint(0, 900), rng.randint(900, 1000), rng.randint(0, 400),
                          rng.randint(400, 500)) for i in range(rng.randint(1, 8))]
        rows = [CohortRow(r.dims, min(r.k_b, r.n_b), r.n_b, min(r.k_d, r.n_d), r.n_d) for r in rows]
        d = decompose_rate(rows)
        assert abs(d.total - d.rate - d.composition) < 1e-12


def test_simpsons_paradox_is_labelled_composition():
    # Both cohorts keep their rates; traffic shifts to the low-approval cohort B, so the total drops.
    tables = {("customer_country",): [row([("customer_country", "A")], 0.95, 7000, 0.95, 500),
                                      row([("customer_country", "B")], 0.70, 7000, 0.70, 1500)]}
    a = analyze(CAND, tables, CFG, 1)
    assert a.label == "mix_shift" and a.decomposition["total"] < -0.05
    assert abs(a.decomposition["rate"]) < 1e-9 and a.decomposition["reconciliation_error"] < 1e-12
    # with two cohorts, A losing share and B gaining it are the same event: either may be named as the locus


def test_mix_shift_locus_is_the_cohort_whose_demand_moved():
    # Like harmless_seasonality: one lower-approval market doubles its volume, every other market keeps its rate.
    markets = {"US": (0.93, 3000, 3000), "GB": (0.90, 1200, 1200), "DE": (0.91, 1200, 1200), "FR": (0.89, 900, 900),
               "BR": (0.78, 1000, 2200), "MX": (0.80, 700, 700)}
    rows = [row([("customer_country", c)], r, nb * 7, r, nd) for c, (r, nb, nd) in markets.items()]
    a = analyze(CAND, {("customer_country",): rows}, CFG, 1)
    assert a.label == "mix_shift" and a.locus == (("customer_country", "BR"),)


def test_rate_change_in_one_cohort_is_localized_with_controls():
    rows = [row([("psp", "alpha")], 0.90, 7000, 0.90, 1000), row([("psp", "beta")], 0.90, 7000, 0.60, 1000),
            row([("psp", "gamma")], 0.90, 7000, 0.91, 1000)]
    country = [row([("customer_country", c)], 0.90 if c != "DE" else 0.90, 7000, r, 1000)
               for c, r in (("US", 0.82), ("DE", 0.78), ("FR", 0.80))]
    a = analyze(CAND, {("psp",): rows, ("customer_country",): country}, CFG, 1)
    assert a.label == "rate_change"
    assert a.locus == (("psp", "beta"),) and not a.locus_is_scope
    assert {dict(t.dims)["psp"] for t in a.controls} <= {"alpha", "gamma"} and a.controls
    assert a.top[0].dims == (("psp", "beta"),)


def test_null_cohorts_give_no_discoveries():
    rng = random.Random(7)
    found = 0
    for trial in range(20):
        rows = []
        for i in range(30):
            per = [(sum(rng.random() < 0.9 for _ in range(200)), 200) for _ in range(7)]
            rows.append(CohortRow((("c", str(i)),), sum(k for k, _ in per), 1400,
                                  sum(rng.random() < 0.9 for _ in range(300)), 300, tuple(per)))
        found += analyze(CAND, {("c",): rows}, CFG, 1).discoveries
    assert found <= 2  # BH at q = 0.05 on 600 null cohorts


def test_small_cohorts_are_never_tested_or_ranked():
    rows = [row([("psp", "tiny")], 0.9, 70, 0.0, 10), row([("psp", "big")], 0.9, 7000, 0.89, 1000)]
    a = analyze(CAND, {("psp",): rows}, CFG, 1)
    assert all(dict(t.dims)["psp"] != "tiny" for t in a.top) and a.cohorts_examined == 1


def test_locus_is_the_scope_when_no_narrower_cohort_explains_the_change():
    cand = dict(CAND, scope=(("psp", "psp_alpha"),))
    rows = [row([("payment_method_type", "card")], 0.90, 7000, 0.62, 1000)]  # psp_alpha carries only cards
    countries = [row([("customer_country", c)], 0.90, 7000, 0.62, 250) for c in ("US", "GB", "BR", "MX")]
    a = analyze(cand, {("payment_method_type",): rows, ("customer_country",): countries}, CFG, 1)
    assert a.locus_is_scope and a.locus == (("psp", "psp_alpha"),)


def test_benjamini_hochberg_matches_a_worked_example():
    p = [0.01, 0.04, 0.03, 0.005, 0.2]
    assert benjamini_hochberg(p, 0.05) == [True, True, True, True, False]
    q = bh_qvalues(p)
    assert abs(q[3] - 0.025) < 1e-12 and abs(q[4] - 0.2) < 1e-12


def test_bundle_is_bounded_labelled_and_deterministic():
    rows = [row([("psp", p)], 0.9, 7000, 0.6 if p == "p0" else 0.9, 1000) for p in ("p0", "p1", "p2", "p3")]
    tables = {("psp",): rows, ("customer_country",): [row([("customer_country", f"C{i}")], 0.9, 7000, 0.82, 400) for i in range(40)]}
    a = analyze(CAND, tables, CFG, 1)
    a.impact = estimate_impact(a, locus_row(a, tables), 1.0)
    b1, b2 = bundle(a, CAND, CFG), bundle(analyze(CAND, tables, CFG, 1) | None if False else a, CAND, CFG)
    assert b1 == b2 and b1["bundle_id"].startswith("evb_")
    assert len(json.dumps(b1)) <= CFG.bundle_max_bytes and len(b1["top_cohorts"]) <= 5 and len(b1["controls"]) <= 3
    assert b1["impact"]["epistemic"] == "estimated" and b1["label"]["epistemic"] == "inferred"
    assert all(t["epistemic"] == "observed" and t["evidence_id"].startswith("evd_") for t in b1["top_cohorts"])
    assert b1["impact"]["measure"] == "lost_successes" and b1["impact"]["value"] > 250  # 0.3 x 1000 lost


def test_count_candidates_decompose_into_deltas():
    cand = dict(CAND, metric="attempt_volume", direction="up")
    rows = [CohortRow((("customer_country", "BR"),), 100.0, 1.0, 220.0, 1.0, tuple((100.0, 1.0) for _ in range(7))),
            CohortRow((("customer_country", "MX"),), 80.0, 1.0, 82.0, 1.0, tuple((80.0, 1.0) for _ in range(7)))]
    a = analyze(cand, {("customer_country",): rows}, CFG, 1)
    assert a.kind == "count" and a.locus == (("customer_country", "BR"),)
    assert a.decomposition["total"] == 122.0


# ------------------------------------------------------------------------------ Gate 1 rules (ADR-038)
def test_large_slice_of_a_degraded_cohort_does_not_replace_it():
    # psp_gamma degrades on cards and pix alike; its card slice (90 % of traffic) explains 90 % of the drop.
    psp = [row([("psp", "alpha")], 0.90, 7000, 0.90, 1000), row([("psp", "gamma")], 0.90, 7000, 0.70, 1000)]
    pair = [row([("psp", "alpha"), ("payment_method_type", "card")], 0.90, 7000, 0.90, 1000),
            row([("psp", "gamma"), ("payment_method_type", "card")], 0.90, 6300, 0.70, 900),
            row([("psp", "gamma"), ("payment_method_type", "pix")], 0.90, 700, 0.70, 100)]
    a = analyze(CAND, {("psp",): psp, ("psp", "payment_method_type"): pair}, CFG, 2)
    assert a.locus == (("psp", "gamma"),)


def test_concentrated_sub_cohort_still_wins():
    # only sepa_debit inside psp_beta degrades: the sub-cohort is a third of the PSP and carries all of the drop
    psp = [row([("psp", "alpha")], 0.90, 7000, 0.90, 1500), row([("psp", "beta")], 0.90, 7000, 0.80, 1500)]
    pair = [row([("psp", "alpha"), ("payment_method_type", "card")], 0.90, 7000, 0.90, 1500),
            row([("psp", "beta"), ("payment_method_type", "card")], 0.90, 4667, 0.90, 1000),
            row([("psp", "beta"), ("payment_method_type", "sepa_debit")], 0.90, 2333, 0.60, 500)]
    a = analyze(CAND, {("psp",): psp, ("psp", "payment_method_type"): pair}, CFG, 2)
    assert a.locus == (("psp", "beta"), ("payment_method_type", "sepa_debit"))


def test_equivalent_dimensions_resolve_by_combination_order_and_drop_constant_dimensions():
    # app_version=web is the same population as platform=web: platform comes first in COMBINATIONS
    plat = [row([("platform", "web")], 0.90, 7000, 0.70, 1000), row([("platform", "ios")], 0.90, 7000, 0.90, 1000)]
    ver = [row([("app_version", "web")], 0.90, 7000, 0.70, 1000), row([("app_version", "5.13.0")], 0.90, 7000, 0.90, 1000)]
    a = analyze(CAND, {("platform",): plat, ("app_version",): ver}, CFG, 2)
    assert a.locus == (("platform", "web"),)
    # card_brand=unknown inside sepa_debit adds nothing: the canonical locus drops it
    pm = [row([("payment_method_type", "sepa_debit")], 0.90, 2000, 0.60, 300), row([("payment_method_type", "card")], 0.90, 12000, 0.90, 1700)]
    pair = [row([("payment_method_type", "sepa_debit"), ("card_brand", "unknown")], 0.90, 2000, 0.60, 300)]
    from anomalyos.cohorts.analysis import canonical_locus
    tables = {("payment_method_type",): pm, ("payment_method_type", "card_brand"): pair}
    assert canonical_locus(pair[0].dims, (), tables) == (("payment_method_type", "sepa_debit"),)


def test_new_cohort_without_baseline_is_compared_with_the_rest_of_the_scope():
    # android checkout: 5.14.0 did not exist before and converts far worse than 5.13.0 in the same window
    cand = dict(CAND, metric="checkout_conversion_rate", scope=(("platform", "android"),))
    ver = [row([("app_version", "5.13.0")], 0.80, 7000, 0.80, 700), CohortRow((("app_version", "5.14.0"),), 0, 0, 150, 300)]
    country = [row([("customer_country", "US")], 0.80, 4000, 0.71, 600), row([("customer_country", "GB")], 0.80, 3000, 0.71, 400)]
    a = analyze(cand, {("app_version",): ver, ("customer_country",): country}, CFG, 2)
    assert a.label == "new_cohort" and a.locus == (("platform", "android"), ("app_version", "5.14.0"))
    assert a.top[0].reference is not None
    imp = estimate_impact(a, locus_row(a, {("app_version",): ver}), 1.0)
    assert imp["measure"] == "lost_successes" and abs(imp["value"] - (0.80 - 0.50) * 300) < 1.0
    b = bundle(a, dict(cand, observed=0.7, expected=0.8), CFG)
    assert b["label"]["value"] == "new_cohort" and b["top_cohorts"][0]["comparison"] == "rest_of_scope_same_window"


def test_injected_traffic_does_not_dilute_its_own_cohort():
    # card testing: psp_alpha is a third of baseline traffic, then receives 3x its legitimate attempts as junk; its
    # share of *during* traffic (2/3) would make the drop look unconcentrated (0.75 / 0.67 < 1.25)
    psp = [row([("psp", "psp_alpha")], 0.90, 3500, 0.30, 2000), row([("psp", "psp_gamma")], 0.90, 7000, 0.90, 1000)]
    a = analyze(CAND, {("psp",): psp}, CFG, 3)
    assert a.locus == (("psp", "psp_alpha"),)


def test_degraded_psp_carrying_most_of_a_country_is_still_named():
    # gradual drift: psp_gamma carries 70 % of ES and loses 10 points; lift 1 / 0.7 = 1.43 >= 1.25
    cand = dict(CAND, scope=(("customer_country", "ES"),))
    pair = [row([("psp", "psp_gamma")], 0.90, 4900, 0.80, 700), row([("psp", "psp_beta")], 0.90, 2100, 0.90, 300)]
    a = analyze(cand, {("psp",): pair}, CFG, 3)
    assert a.locus == (("customer_country", "ES"), ("psp", "psp_gamma"))
