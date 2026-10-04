"""Hand-built first-look candidates and Stage 4 bundles for Stage 5 tests (no ClickHouse, no ground truth)."""

from __future__ import annotations

H = 3600
T = 1_785_715_200 + 6 * 86_400


def candidate(**kw):
    c = {"anomaly_id": "anom_t1", "system": "main", "series_key": "S2", "metric": "authorization_rate", "metric_version": 1,
         "grain": "1h", "scope": (("psp", "psp_beta"),), "direction": "down", "window_start": T, "window_end": T + H,
         "detected_at": T + H, "observed": 0.62, "expected": 0.9, "delta": -0.28, "relative_delta": -0.311,
         "score": 9.1, "sample_size": 1200, "method": ("z_persistence",), "evidence_ids": ("evd_first",),
         "status": "promoted", "status_reason": "", "recovered_at": None, "parent_id": None,
         "score_at_detection": 9.1, "methods_at_detection": ("z_persistence",)}
    c.update(kw)
    return c


def bundle(**kw):
    b = {"bundle_id": "evb_1", "label": {"value": "rate_change", "combination": ["payment_method_type"]},
         "decomposition": {"total": -0.1, "rate": -0.1, "composition": 0.0},
         "locus": {"cohort": [["psp", "psp_beta"], ["payment_method_type", "sepa_debit"]], "is_scope": False},
         "top_cohorts": [{"cohort": [["psp", "psp_beta"], ["payment_method_type", "sepa_debit"]], "z": -12.0,
                          "contribution": -0.095, "evidence_id": "evd_top"}],
         "controls": [{"cohort": [["psp", "psp_beta"], ["payment_method_type", "card"]], "z": 0.3, "evidence_id": "evd_ctl"}],
         "related_metrics": [{"metric": "checkout_conversion_rate", "z": -3.1, "evidence_id": "evd_rel"}],
         "impact": {"measure": "lost_successes", "value": 340.0, "interval": [280.0, 400.0], "epistemic": "estimated"},
         "cohorts_examined": 40, "discoveries": 3}
    b.update(kw)
    return b
