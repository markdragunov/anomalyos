"""No-Jev baseline (ADR-039 D-7, ADR-040): routes from Stage 3/4 evidence alone. A benchmark control for Jev, **not**
the failure fallback (that is always DIGEST). Pure and versioned; the thresholds live in ``BaselineConfig``."""

from __future__ import annotations

from anomalyos.jev.state import JevState
from anomalyos.policy.config import BaselineConfig


STRENGTH = ("weak", "moderate", "strong")
IMPACT = ("negligible", "small", "medium", "large")
SHARE = ("not_provided", "minor", "partial", "most", "nearly_all")


def route(state: JevState, cfg: BaselineConfig = BaselineConfig()) -> tuple[str, str]:
    if (state.strength in STRENGTH and STRENGTH.index(state.strength) >= STRENGTH.index(cfg.strength_min)
            and state.change_type in cfg.change_types
            and state.impact in IMPACT and IMPACT.index(state.impact) >= IMPACT.index(cfg.impact_min)
            and SHARE.index(state.locus_share) >= SHARE.index(cfg.locus_share_min)):
        return "INCIDENT", "b1_strong_localized_rate_change"
    if state.change_type in ("mix_shift", "volume_only") and state.strength == "weak":
        return "IGNORE", "b2_weak_composition"
    return "DIGEST", "b3_otherwise"
