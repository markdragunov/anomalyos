"""Mode A over a set of Stage 3 candidates (ADR-039 D-0): first-look view -> Stage 4 on the as-of window -> decide.

Input: a query runner, database, run id, Stage 3 candidates, world start, a ``JevClient``, the pinned model.
Output: one ``ModeADecision`` (record, first-look candidate, Stage 4 analysis and bundle) per decided candidate,
in detection order.
Decided: ``system == "main"`` candidates that were promoted (recovered ones are viewed as of detection); suppressed
candidates and the static comparison system are not. Stage 4 is called unchanged, with the as-of window
``[window_start, detected_at)``; ClickHouse is reached only through it (INV-003). Nothing reads ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from anomalyos.cohorts import CohortAnalysis, CohortConfig, analyze_candidates
from anomalyos.detection.candidates import AnomalyCandidate, as_of_view, concurrent_at
from anomalyos.jev.client import DecisionContext, JevClient
from anomalyos.policy.config import PolicyConfig
from anomalyos.policy.decide import DecisionRecord, decide


@dataclass(frozen=True)
class ModeADecision:
    record: DecisionRecord
    candidate: AnomalyCandidate  # first-look view
    analysis: CohortAnalysis | None
    bundle: dict[str, Any] | None


def decidable(candidates: Iterable[AnomalyCandidate]) -> list[AnomalyCandidate]:
    return sorted((c for c in candidates if c.system == "main" and c.status in ("promoted", "recovered")),
                  key=lambda c: (c.detected_at, c.anomaly_id))


def run(runner, database: str, run_id: str, candidates: Iterable[AnomalyCandidate], world_start: int, client: JevClient,
        pinned_model: str, cfg: PolicyConfig = PolicyConfig(), cohort_cfg: CohortConfig = CohortConfig(),
        freshness_s: int = 900) -> list[ModeADecision]:
    full = decidable(candidates)
    views = [as_of_view(c) for c in full]
    stage4 = {a.candidate_id: (a, b) for a, b in analyze_candidates(runner, database, run_id, views, world_start, cohort_cfg)}
    out = []
    for c, v in zip(full, views):
        ctx = DecisionContext(as_of=v.detected_at, evaluated_at=v.detected_at, freshness_s=freshness_s)
        analysis, bundle = stage4.get(v.anomaly_id, (None, None))
        record = decide(v, bundle, concurrent_at(c, full), client, ctx, pinned_model, cfg)
        out.append(ModeADecision(record, v, analysis, bundle))
    return out
