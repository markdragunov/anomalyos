"""baseline_v1 (ADR-039 D-7): routes from Stage 3/4 evidence alone. A benchmark control for Jev, **not** the failure
fallback (that is always DIGEST). Pure and versioned."""

from __future__ import annotations

from anomalyos.jev.state import JevState
from anomalyos.policy.config import BaselineConfig


def route(state: JevState, cfg: BaselineConfig = BaselineConfig()) -> tuple[str, str]:
    if (state.strength in ("moderate", "strong") and state.change_type in ("rate_change", "new_cohort", "mixed")
            and state.impact in ("medium", "large") and state.locus_share in ("partial", "most", "nearly_all")):
        return "INCIDENT", "b1_strong_localized_rate_change"
    if state.change_type in ("mix_shift", "volume_only") and state.strength == "weak":
        return "IGNORE", "b2_weak_composition"
    return "DIGEST", "b3_otherwise"
