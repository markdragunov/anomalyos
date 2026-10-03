"""Pure cohort analysis over pooled before / during tables (ADR-037, design C-3 ... C-7). No I/O.

Input: per combination, rows ``(cohort dims, before numerator, before denominator, during numerator, during
denominator, per-reference-day values)`` for the candidate's scope; the parent's direction and kind.
Output: ``CohortAnalysis`` — exact rate / composition decomposition, BH-controlled discoveries, locus, controls,
label, estimated impact — and the bounded ``EvidenceBundle`` dict.
Invariants
* rate effect + composition effect == total change (midpoint decomposition), per combination;
* only cohorts with support in both periods are tested or ranked; the multiple-testing denominator is reported;
* impact is labelled ``estimated``; nothing here reads ground truth (INV-015).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Sequence

from anomalyos.cohorts.config import CohortConfig

Dims = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CohortRow:
    dims: Dims
    k_b: float  # before numerator (pooled over reference days, already divided by the number of days for counts)
    n_b: float  # before denominator (rates) / 1 (counts)
    k_d: float
    n_d: float
    ref_days: tuple[tuple[float, float], ...] = ()  # per reference day (numerator, denominator), for phi


def _h(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16]


def evidence_id(*parts: object) -> str:
    return "evd_" + _h(*parts)


def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


# ------------------------------------------------------------------------------ statistics
def z_rate(k_b: float, n_b: float, k_d: float, n_d: float, phi: float = 1.0) -> float | None:
    """Two-sample z on the arcsine scale (variance of 2*asin(sqrt(p)) ~ 1/n). Clamped to [0, 1] for ratio metrics."""
    if n_b <= 0 or n_d <= 0:
        return None
    pb, pd = min(1.0, k_b / n_b), min(1.0, k_d / n_d)
    return 2 * (math.asin(math.sqrt(pd)) - math.asin(math.sqrt(pb))) / math.sqrt(phi * (1 / n_b + 1 / n_d))


def z_count(mean_b: float, m: int, x_d: float, phi: float = 1.0) -> float | None:
    """Anscombe z of an observed count against the mean of m reference days."""
    if m <= 0:
        return None
    return 2 * (math.sqrt(x_d + 0.375) - math.sqrt(mean_b + 0.375)) / math.sqrt(phi * (1 + 1 / m))


def phi_from_reference_days(rows: Sequence[CohortRow], kind: str, min_support: int) -> float:
    """Robust overdispersion from the reference days: each day against the pooled others, per supported cohort."""
    zs = []
    for r in rows:
        days = list(r.ref_days)
        if len(days) < 3:
            continue
        for i, (k, n) in enumerate(days):
            rest = days[:i] + days[i + 1:]
            if kind == "count":
                z = z_count(sum(x for x, _ in rest) / len(rest), len(rest), k)
            elif n >= min_support:
                z = z_rate(sum(x for x, _ in rest), sum(y for _, y in rest), k, n)
            else:
                z = None
            if z is not None:
                zs.append(z)
    if len(zs) < 6:
        return 1.0
    return max(1.0, (median(abs(z) for z in zs) / 0.6745) ** 2)


def benjamini_hochberg(pvals: Sequence[float], q: float) -> list[bool]:
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    cutoff = -1
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= q * rank / m:
            cutoff = rank
    keep = [False] * m
    for rank, i in enumerate(order, start=1):
        if rank <= cutoff:
            keep[i] = True
    return keep


def bh_qvalues(pvals: Sequence[float]) -> list[float]:
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    q = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvals[i] * m / rank)
        q[i] = running
    return q


# ------------------------------------------------------------------------------ decomposition
@dataclass
class Decomposition:
    total: float
    rate: float
    composition: float
    per_cohort: dict[Dims, tuple[float, float]]  # dims -> (rate part, composition part)

    @property
    def reconciliation_error(self) -> float:
        return abs(self.total - self.rate - self.composition)

    @property
    def rate_share(self) -> float | None:
        s = abs(self.rate) + abs(self.composition)
        return abs(self.rate) / s if s > 0 else None


def decompose_rate(rows: Sequence[CohortRow]) -> Decomposition:
    """R = sum_i w_i r_i; dR = sum w_bar_i * dr_i + sum r_bar_i * dw_i (exact). Absent cohorts take the other rate."""
    nb, nd = sum(r.n_b for r in rows), sum(r.n_d for r in rows)
    if nb <= 0 or nd <= 0:
        return Decomposition(0.0, 0.0, 0.0, {})
    R_b = sum(r.k_b for r in rows) / nb
    R_d = sum(r.k_d for r in rows) / nd
    R_mid = (R_b + R_d) / 2
    per: dict[Dims, tuple[float, float]] = {}
    for r in rows:
        wb, wd = r.n_b / nb, r.n_d / nd
        rb = r.k_b / r.n_b if r.n_b > 0 else (r.k_d / r.n_d if r.n_d > 0 else 0.0)
        rd = r.k_d / r.n_d if r.n_d > 0 else rb
        # composition part centred on the mean rate: sums to the same total (sum of dw is 0) but attributes a mix
        # shift to the cohorts whose share moved *and* whose rate differs from the rest
        per[r.dims] = ((wb + wd) / 2 * (rd - rb), ((rb + rd) / 2 - R_mid) * (wd - wb))
    return Decomposition(R_d - R_b, sum(a for a, _ in per.values()), sum(b for _, b in per.values()), per)


def decompose_count(rows: Sequence[CohortRow]) -> Decomposition:
    per = {r.dims: (r.k_d - r.k_b, 0.0) for r in rows}
    total = sum(a for a, _ in per.values())
    return Decomposition(total, total, 0.0, per)


# ------------------------------------------------------------------------------ analysis
@dataclass
class TestedCohort:
    combination: tuple[str, ...]
    dims: Dims
    row: CohortRow
    z: float
    p: float
    q: float = 1.0
    discovery: bool = False
    contribution: float = 0.0  # rate part (rates) / delta count (counts), in parent units
    coverage: float = 0.0


@dataclass
class CohortAnalysis:
    candidate_id: str
    metric: str
    metric_version: int
    kind: str
    direction: str
    scope: Dims
    window: tuple[int, int]
    config_version: int
    label: str
    label_combination: tuple[str, ...] | None
    decomposition: dict[str, float]
    locus: Dims | None
    locus_is_scope: bool
    top: list[TestedCohort]
    controls: list[TestedCohort]
    cohorts_examined: int
    discoveries: int
    phi: dict[str, float]
    impact: dict[str, Any] | None
    related_metrics: list[dict[str, Any]] = field(default_factory=list)
    related_candidates: list[str] = field(default_factory=list)
    queries: int = 0
    flags: list[str] = field(default_factory=list)


def _sign(direction: str) -> int:
    return -1 if direction == "down" else 1


def analyze(candidate: dict, tables: dict[tuple[str, ...], list[CohortRow]], cfg: CohortConfig,
            config_version: int) -> CohortAnalysis:
    """Rank cohorts, decompose the change, choose locus and controls. ``tables`` maps combination -> rows."""
    kind = "count" if candidate["metric"] in ("attempt_volume", "refund_count") else "rate"
    sgn = _sign(candidate["direction"])
    scope: Dims = tuple(tuple(x) for x in candidate["scope"])
    flags: list[str] = []
    tested: list[TestedCohort] = []
    decomps: dict[tuple[str, ...], Decomposition] = {}
    phis: dict[str, float] = {}
    parent_total = None
    for combo, rows in tables.items():
        rows = rows[: cfg.max_cohorts_per_combination]
        dec = decompose_count(rows) if kind == "count" else decompose_rate(rows)
        decomps[combo] = dec
        parent_total = dec.total if parent_total is None else parent_total
        phi = phi_from_reference_days(rows, kind, cfg.min_support)
        phis["*".join(combo)] = round(phi, 3)
        for r in rows:
            if kind == "rate":
                if r.n_b < cfg.min_support or r.n_d < cfg.min_support:
                    continue
                z = z_rate(r.k_b, r.n_b, r.k_d, r.n_d, phi)
            else:
                if r.k_b + r.k_d < cfg.min_count_events:
                    continue
                z = z_count(r.k_b, max(1, len(r.ref_days)), r.k_d, phi)
            if z is None:
                continue
            tested.append(TestedCohort(combo, scope + r.dims, r, z, norm_sf(sgn * z), contribution=dec.per_cohort[r.dims][0]))
    if not tested:
        flags.append("insufficient_data")
    for t, keep, qv in zip(tested, benjamini_hochberg([t.p for t in tested], cfg.bh_q), bh_qvalues([t.p for t in tested])):
        t.discovery, t.q = keep, qv
    total = parent_total or 0.0
    for t in tested:
        t.coverage = t.contribution / total if total else 0.0  # same sign as the parent change -> positive

    # Label: the combination that explains the change best is the one with the smallest rate share (a composition
    # artefact disappears at the level that separates the mixing cohorts; a real rate change persists everywhere).
    label, label_combo = "insufficient_data", None
    if kind == "rate":
        shares = [(dec.rate_share, combo) for combo, dec in decomps.items()
                  if dec.rate_share is not None and sum(1 for r in tables[combo]
                                                        if r.n_b >= cfg.min_support and r.n_d >= cfg.min_support) >= 2]
        if shares:
            share, label_combo = min(shares, key=lambda x: (x[0], x[1]))
            label = ("rate_change" if share >= cfg.rate_share_high else "mix_shift" if share <= cfg.rate_share_low
                     else "mixed")
    else:
        label = "rate_change"  # refined to volume_only by the caller from the locus's approval (design C-4)

    # Locus: among discoveries that explain >= coverage of the change in its direction, the most specific (fewest
    # during attempts, then fewer dimensions). The candidate's own scope is the coarsest such cohort.
    eligible = [t for t in tested if t.discovery and t.coverage >= cfg.locus_coverage]
    parent_n = max((sum(r.n_d for r in rows) for rows in tables.values()), default=0)
    if kind == "rate" and label == "mix_shift" and label_combo is not None:
        comp = decomps[label_combo].per_cohort
        nb_all = sum(r.n_b for r in tables[label_combo]) or 1.0
        nd_all = sum(r.n_d for r in tables[label_combo]) or 1.0
        # the cohort pushing the total hardest in the parent's direction; ties: the one whose share grew
        best = max(tables[label_combo], key=lambda r: (round(sgn * comp[r.dims][1], 12), r.n_d / nd_all - r.n_b / nb_all, r.dims))
        locus, locus_is_scope = scope + best.dims, False
    elif eligible:
        best_t = min(eligible, key=lambda t: (t.row.n_d if kind == "rate" else t.row.k_d, len(t.dims), t.dims))
        if kind == "rate" and best_t.row.n_d >= 0.98 * parent_n:
            locus, locus_is_scope = scope, True  # the "sub-cohort" is the whole scope (e.g. psp_alpha = cards)
        else:
            locus, locus_is_scope = best_t.dims, False
    else:
        locus, locus_is_scope = (scope, True) if tested else (None, False)

    # rank by contribution *in the parent's direction* (a cohort that moved the other way does not explain the change)
    ranked = sorted((t for t in tested if t.discovery), key=lambda t: (-sgn * t.contribution, -abs(t.z), t.dims))
    top = ranked[: cfg.top_k]

    controls: list[TestedCohort] = []
    if locus and not locus_is_scope:
        loc = dict(locus)
        for t in tested:
            d = dict(t.dims)
            if set(d) != set(loc) or abs(t.z) >= cfg.control_z:
                continue
            if sum(1 for k in loc if d[k] != loc[k]) == 1:
                controls.append(t)
        controls = sorted(controls, key=lambda t: (-(t.row.n_d if kind == "rate" else t.row.k_d), t.dims))[: cfg.max_controls]
        if not controls:
            flags.append("no_control")

    best_dec = decomps.get(label_combo) if label_combo else (next(iter(decomps.values())) if decomps else None)
    decomposition = ({"total": round(best_dec.total, 6), "rate": round(best_dec.rate, 6),
                      "composition": round(best_dec.composition, 6),
                      "reconciliation_error": round(best_dec.reconciliation_error, 12)} if best_dec else {})
    return CohortAnalysis(
        candidate_id=candidate["anomaly_id"], metric=candidate["metric"], metric_version=candidate["metric_version"],
        kind=kind, direction=candidate["direction"], scope=scope, window=(candidate["window_start"], candidate["window_end"]),
        config_version=config_version, label=label, label_combination=label_combo, decomposition=decomposition,
        locus=locus, locus_is_scope=locus_is_scope, top=top, controls=controls, cohorts_examined=len(tested),
        discoveries=sum(t.discovery for t in tested), phi=phis, impact=None, flags=flags)


def locus_row(analysis: CohortAnalysis, tables: dict[tuple[str, ...], list[CohortRow]]) -> CohortRow | None:
    if analysis.locus is None:
        return None
    want = dict(analysis.locus)
    scope = dict(analysis.scope)
    for combo, rows in tables.items():
        for r in rows:
            if dict(r.dims) | scope == want:
                return r
    if analysis.locus_is_scope:
        rows = next(iter(tables.values()), [])
        return CohortRow((), sum(r.k_b for r in rows), sum(r.n_b for r in rows), sum(r.k_d for r in rows),
                         sum(r.n_d for r in rows), ())
    return None


def estimate_impact(analysis: CohortAnalysis, row: CohortRow | None, phi: float) -> dict[str, Any] | None:
    """Estimated, never observed: lost successes (rates, down) or excess events (rates up / counts)."""
    if row is None:
        return None
    if analysis.kind == "count":
        excess = row.k_d - row.k_b
        sd = math.sqrt(phi * (row.k_d + row.k_b))
        return {"epistemic": "estimated", "measure": "excess_events", "value": round(excess, 2),
                "interval": [round(excess - 1.96 * sd, 2), round(excess + 1.96 * sd, 2)]}
    if row.n_b <= 0 or row.n_d <= 0:
        return None
    rb, rd = min(1.0, row.k_b / row.n_b), min(1.0, row.k_d / row.n_d)
    diff = (rb - rd) if analysis.direction == "down" else (rd - rb)
    sd = math.sqrt(phi * (rb * (1 - rb) / row.n_b + rd * (1 - rd) / row.n_d)) * row.n_d
    measure = "lost_successes" if analysis.direction == "down" else "excess_events"
    value = diff * row.n_d
    return {"epistemic": "estimated", "measure": measure, "value": round(value, 2),
            "interval": [round(value - 1.96 * sd, 2), round(value + 1.96 * sd, 2)]}


def _cohort_item(t: TestedCohort, a: CohortAnalysis) -> dict[str, Any]:
    r = t.row
    item = {"cohort": [list(x) for x in t.dims], "z": round(t.z, 3), "q_value": round(t.q, 6),
            "contribution": round(t.contribution, 6), "epistemic": "observed",
            "evidence_id": evidence_id(a.metric, a.metric_version, t.dims, a.window)}
    if a.kind == "rate":
        item.update(attempts_before=round(r.n_b, 1), attempts_during=round(r.n_d, 1),
                    rate_before=round(r.k_b / r.n_b, 6) if r.n_b else None, rate_during=round(r.k_d / r.n_d, 6) if r.n_d else None)
    else:
        item.update(mean_before=round(r.k_b, 2), during=round(r.k_d, 2))
    return item


def bundle(a: CohortAnalysis, candidate: dict, cfg: CohortConfig) -> dict[str, Any]:
    """The bounded EvidenceBundle for Stage 5 (design C-1)."""
    b = {
        "bundle_id": "evb_" + _h(a.candidate_id, a.config_version),
        "config_version": a.config_version,
        "candidate": {"anomaly_id": a.candidate_id, "metric": a.metric, "metric_version": a.metric_version,
                      "scope": [list(x) for x in a.scope], "window": list(a.window), "direction": a.direction,
                      "observed": candidate["observed"], "expected": candidate["expected"], "epistemic": "observed",
                      "evidence_ids": list(candidate["evidence_ids"][:3])},
        "label": {"value": a.label, "combination": list(a.label_combination or []), "epistemic": "inferred"},
        "decomposition": dict(a.decomposition, epistemic="observed"),
        "locus": {"cohort": [list(x) for x in a.locus] if a.locus is not None else None, "is_scope": a.locus_is_scope,
                  "epistemic": "inferred"},
        "top_cohorts": [_cohort_item(t, a) for t in a.top[: cfg.top_k]],
        "controls": [_cohort_item(t, a) for t in a.controls[: cfg.max_controls]],
        "related_metrics": a.related_metrics[:2],
        "related_candidates": a.related_candidates[:5],
        "impact": a.impact,
        "cohorts_examined": a.cohorts_examined,
        "discoveries": a.discoveries,
        "queries": a.queries,
        "flags": a.flags,
    }
    size = len(json.dumps(b, separators=(",", ":")))
    if size > cfg.bundle_max_bytes:
        raise ValueError(f"evidence bundle of {size} bytes exceeds {cfg.bundle_max_bytes}")
    return b
