"""Ground-truth vocabulary and records for injected scenarios.

Why: every later stage (detection, routing, cohort isolation, investigation, Jev gating) is
scored against *what actually happened*. That answer key must be produced by deterministic
simulator code from the scenario spec and the simulator's own counterfactual bookkeeping —
never by Jev, an LLM, or by re-analysing the emitted events.

Input:  ``TruthSpec`` (designer intent, part of a scenario) + ``EffectTally`` (counterfactual
        counts accumulated by the engine while it generated the data).
Output: ``GroundTruth`` records, JSON-serialisable, stored apart from events (separate file and
        separate ClickHouse database) so analytics/agent code cannot read them by accident.

Invariants
* Closed vocabularies: ``RootCause``, ``Route``, ``Severity``. ``true_cause == root_cause.cause``.
* An incident record (route ``incident``/``watch``) has an ``incident_id``; a pure noise /
  control record has ``incident_id = None`` and route ``suppress``.
* ``affected_cohorts`` and ``control_cohorts`` never share a cohort.
* Scenario/incident IDs never appear inside event payloads (checked by ``validate``).
Failure modes: ``build_ground_truth`` raises ``ValueError`` on an inconsistent spec.

``RootCause`` is the closed cause vocabulary v1 from docs/DATA_MODEL.md (a unit test enforces
the match). Records carry both this contract's field names and the DATA_MODEL ground-truth
field names (``is_incident``, ``onset_at``, ``ramp``, ``true_impact`` …).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .ids import short_hash, stable_id
from .world import iso

GENERATOR_VERSION = "sim-1.2.1"  # 1.1.0: ADR-029/030; 1.1.1: ADR-031; 1.2.0: realism v2, new scenarios, unchanged_metrics (ADR-032/033); 1.2.1: refund_rate not 'unchanged' for approval incidents


class RootCause(str, Enum):
    """Cause vocabulary v1 — identical to docs/DATA_MODEL.md so hypothesis accuracy is scored on
    the same labels Jev chooses from. Finer scenario semantics live in ``scenario_kind``."""

    PSP_DEGRADATION = "psp_degradation"
    PAYMENT_METHOD_DEGRADATION = "payment_method_degradation"
    ISSUER_OR_COUNTRY_DEGRADATION = "issuer_or_country_degradation"
    CHECKOUT_REGRESSION = "checkout_regression"
    RENEWAL_JOB_FAILURE = "renewal_job_failure"
    DUNNING_FAILURE = "dunning_failure"
    REFUND_PROCESS_CHANGE = "refund_process_change"
    DUPLICATE_CHARGING = "duplicate_charging"
    FRAUD_ATTACK = "fraud_attack"
    PRICING_OR_PLAN_CHANGE = "pricing_or_plan_change"
    DATA_PIPELINE_ISSUE = "data_pipeline_issue"
    NORMAL_VARIATION = "normal_variation"
    UNKNOWN = "unknown"


CAUSE_VOCABULARY_VERSION = "v1"


class Route(str, Enum):
    """What AnomalyOS is expected to do with the signal."""

    INCIDENT = "incident"  # open an incident, notify a human
    WATCH = "watch"  # real but below incident bar: track, no page
    SUPPRESS = "suppress"  # noise / explained variation: no alert


class Severity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Mechanism(str, Enum):
    APPROVAL = "approval"  # multiplies authorization success probability
    ABANDON = "abandon"  # adds probability that a created PaymentIntent is never confirmed
    REFUND = "refund"  # adds refund probability with a short lag
    DUPLICATE = "duplicate"  # adds probability of a second, non-idempotent charge
    VOLUME = "volume"  # multiplies organic checkout demand
    INJECT = "inject"  # injects an exogenous traffic stream (card testing)
    CHURN = "churn"  # sim-1.2: probability that a subscription is canceled at renewal instead of renewing
    DELAY = "delay"  # sim-1.2: share of a cohort's events delivered late (delivered_at), payments unaffected


Cohort = Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class TruthSpec:
    """Designer-declared truth for one incident or one explicitly-harmless signal."""

    key: str
    effect_ids: tuple[str, ...]
    true_cause: RootCause
    expected_route: Route
    severity: Severity
    start: int
    end: int | None  # None = still ongoing at the end of the world
    locus: Cohort  # where the cause lives (e.g. {"psp": ("psp_beta",)})
    mechanism: str  # human-readable causal mechanism
    affected_cohorts: tuple[Cohort, ...]
    control_cohorts: tuple[Cohort, ...]
    primary_metric: str
    expected_direction: str  # "down" | "up" | "none"
    detection_delay_s: int | None  # expected detection window = [start, start + delay]
    expected_recovery: int | None
    unrelated_to: tuple[str, ...] = ()  # keys of co-occurring, causally unrelated records
    affected_metrics: tuple[str, ...] = ()  # DATA_MODEL: metrics the perturbation should move
    unchanged_metrics: tuple[str, ...] = ()  # sim-1.2: metrics that must NOT move (negative evidence for diagnosis)

    def __post_init__(self) -> None:
        is_incident = self.expected_route is not Route.SUPPRESS
        if is_incident and self.detection_delay_s is None:
            raise ValueError(f"{self.key}: incident needs a detection window")
        if not is_incident and self.detection_delay_s is not None:
            raise ValueError(f"{self.key}: suppressed signal must not have a detection window")
        a = {_cohort_key(c) for c in self.affected_cohorts}
        b = {_cohort_key(c) for c in self.control_cohorts}
        if a & b:
            raise ValueError(f"{self.key}: affected and control cohorts overlap")
        if self.end is not None and self.end < self.start:
            raise ValueError(f"{self.key}: end before start")


def _cohort_key(c: Cohort) -> tuple:
    return tuple(sorted((k, tuple(sorted(v))) for k, v in c.items()))


def cohort_json(c: Cohort) -> dict[str, list[str]]:
    return {k: sorted(v) for k, v in sorted(c.items())}


class EffectTally:
    """Counterfactual counters per effect, filled by the engine (actual vs. without-effect)."""

    def __init__(self) -> None:
        self.counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.amounts: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        # Per-hour copy of the same counters (sim-1.1.1), used for `oracle_detectable_at`. The engine sets
        # `hour` to the payment's hour before tallying it and clears it afterwards.
        self.hourly: dict[str, dict[int, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        self.hour: int | None = None

    def add(self, effect_id: str, metric: str, n: int = 1) -> None:
        self.counts[effect_id][metric] += n
        if self.hour is not None:
            self.hourly[effect_id][self.hour][metric] += n

    def add_amount(self, effect_id: str, metric: str, currency: str, amount: int) -> None:
        self.amounts[effect_id][metric][currency] += amount

    def snapshot(self, effect_id: str) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
        c = dict(sorted(self.counts.get(effect_id, {}).items()))
        a = {m: dict(sorted(v.items())) for m, v in sorted(self.amounts.get(effect_id, {}).items())}
        return c, a


@dataclass(frozen=True)
class GroundTruth:
    scenario_id: str
    scenario_kind: str
    record_key: str
    incident_id: str | None
    start: int
    end: int | None
    true_cause: str
    root_cause: dict[str, Any]
    expected_route: str
    severity: str
    affected_cohorts: list[dict[str, list[str]]]
    control_cohorts: list[dict[str, list[str]]]
    expected_metric_effect: dict[str, Any]
    expected_impact: dict[str, Any]
    expected_detection_window: dict[str, Any] | None
    expected_recovery: int | None
    unrelated_to: list[str]
    scenario_seed: int
    spec_hash: str
    # DATA_MODEL.md ground-truth fields (aliases/derivations of the above, same record).
    scenario_type: str
    seed: int
    is_incident: bool
    onset_at: int
    end_at: int | None
    ramp: str
    affected_metrics: list[str]
    injected_effect: dict[str, Any]
    true_impact: dict[str, Any]
    oracle_detectable_at: int | None = None  # sim-1.1.1, ADR-031
    unchanged_metrics: list[str] = field(default_factory=list)  # sim-1.2.0, ADR-033
    oracle_method: str | None = None
    cause_vocabulary_version: str = CAUSE_VOCABULARY_VERSION
    generator_version: str = GENERATOR_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["start_iso"] = iso(self.start)
        d["end_iso"] = iso(self.end) if self.end is not None else None
        return d


RAMPS = ("none", "step", "gradual", "recovering")


def ramp_of(profile: tuple[tuple[int, float], ...] | None) -> str:
    """DATA_MODEL ``ramp`` from an intensity profile: step · gradual · recovering."""
    if not profile:
        return "none"
    if profile[0][1] == 0.0:
        return "gradual"
    if profile[-1][1] == 0.0:
        return "recovering"
    return "step"


ORACLE_Z = 3.0
# Which counters the oracle compares, per primary metric. Rates: (denominator, numerator) actual vs counterfactual,
# one-sided in the declared direction. Counts: the effect's excess against the cohort's organic baseline.
_ORACLE_RATE = {
    "charge_approval_rate": ("attempts", "success"),
    "payment_intent_conversion_rate": ("pi", "pi_succeeded"),
    "renewal_success_rate": ("renewals", "renewal_first_ok"),
    "dunning_recovery_rate": ("pi", "pi_succeeded"),
}
_ORACLE_COUNT = {
    "refund_rate": ("extra_refunds", "pi_succeeded_cf", "organic_refund_rate"),
    "duplicate_charge_rate": ("duplicate_charges", "pi_succeeded_cf", "organic_duplicate_rate"),
    "charge_attempt_volume": ("injected_attempts", "cohort_attempts", None),
    "subscription_cancellation_rate": ("extra_cancellations", "renewal_subs", "organic_cancellation_rate"),
    "ingestion_delay": ("delayed_events", None, None),
}


def oracle_detectability(primary_metric: str, hourly: Mapping[int, Mapping[str, int]], start: int,
                         organic: Mapping[str, float]) -> tuple[int | None, str | None]:
    """First time the cumulative difference between the actual and the counterfactual world reaches z >= 3.

    Computed from the engine's paired counters (identical random draws with and without the effect), in hourly
    steps from the record's start; returns (end of the first hour that crosses, method) or (None, method)."""
    import math

    hours = sorted(h for h in hourly if (h + 1) * 3600 > start)
    if primary_metric in _ORACLE_RATE:
        den, num = _ORACLE_RATE[primary_metric]
        method = f"cumulative one-sided binomial z of {num}/{den} actual vs counterfactual rate, z={ORACLE_Z:g}, hourly"
        n_a = k_a = n_c = k_c = 0
        for h in hours:
            c = hourly[h]
            n_a += c.get(f"{den}_actual", c.get(den, 0)); k_a += c.get(f"{num}_actual", 0)
            n_c += c.get(f"{den}_cf", c.get(den, 0)); k_c += c.get(f"{num}_cf", 0)
            if n_a and n_c:
                p = k_c / n_c
                if 0 < p < 1:
                    z = (p * n_a - k_a) / math.sqrt(n_a * p * (1 - p))
                    if z >= ORACLE_Z:
                        return max(start, (h + 1) * 3600), method
        return None, method
    if primary_metric in _ORACLE_COUNT:
        excess_key, base_key, rate_key = _ORACLE_COUNT[primary_metric]
        method = (f"cumulative Poisson z of {excess_key} over the cohort's organic baseline, z={ORACLE_Z:g}, hourly; "
                  "counted at payment time")
        x = b = 0.0
        for h in hours:
            c = hourly[h]
            x += c.get(excess_key, 0)
            b += (c.get(base_key, 0) * (organic[rate_key] if rate_key else 1.0)) if base_key else 0.0
            if x and x / math.sqrt(max(b, 1.0)) >= ORACLE_Z:
                return max(start, (h + 1) * 3600), method
        return None, method
    return None, None


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def build_ground_truth(
    *, world_seed: int, scenario_id: str, scenario_kind: str, scenario_seed: int, spec_hash: str,
    truth: TruthSpec, effect_params: Mapping[str, Mapping[str, Any]], tally: EffectTally,
    unrelated_incident_ids: list[str], world_end: int,
    profile: tuple[tuple[int, float], ...] | None = None,
    organic: Mapping[str, float] | None = None,
    world_hourly: Mapping[int, tuple[int, int]] | None = None,
) -> GroundTruth:
    is_incident = truth.expected_route is not Route.SUPPRESS
    incident_id = stable_id("inc", world_seed, scenario_id, truth.key, length=16) if is_incident else None

    counts: dict[str, int] = defaultdict(int)
    amounts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for eid in truth.effect_ids:
        c, a = tally.snapshot(eid)
        for k, v in c.items():
            counts[k] += v
        for m, per in a.items():
            for cur, v in per.items():
                amounts[m][cur] += v

    measured = {
        "charge_attempts": counts["attempts_actual"],
        "charge_approval_rate_actual": _rate(counts["success_actual"], counts["attempts_actual"]),
        "charge_approval_rate_counterfactual": _rate(counts["success_cf"], counts["attempts_cf"]),
        "payment_intents": counts["pi_actual"],
        "pi_conversion_actual": _rate(counts["pi_succeeded_actual"], counts["pi_actual"]),
        "pi_conversion_counterfactual": _rate(counts["pi_succeeded_cf"], counts["pi_cf"]),
    }
    if not truth.effect_ids and world_hourly is not None:
        # Records without an effect (the control day): reference values of a normal day, whole world.
        h0, h1 = truth.start // 3600, (truth.end if truth.end is not None else world_end) // 3600
        n = sum(world_hourly.get(h, (0, 0))[0] for h in range(h0, h1))
        k = sum(world_hourly.get(h, (0, 0))[1] for h in range(h0, h1))
        measured.update(charge_attempts=n, charge_approval_rate_actual=_rate(k, n), basis="whole world, no injected mechanism")
    if counts.get("renewals"):
        measured["renewals"] = counts["renewals"]
        measured["renewal_first_attempt_success_actual"] = _rate(counts["renewal_first_ok_actual"], counts["renewals"])
        measured["renewal_first_attempt_success_counterfactual"] = _rate(counts["renewal_first_ok_cf"], counts["renewals"])
    if counts.get("global_attempts_actual"):
        measured["global_charge_approval_rate_actual"] = _rate(counts["global_success_actual"], counts["global_attempts_actual"])
        measured["global_charge_approval_rate_counterfactual"] = _rate(counts["global_success_cf"], counts["global_attempts_cf"])
    for k in ("extra_refunds", "duplicate_charges", "injected_attempts", "injected_succeeded", "extra_payment_intents"):
        if counts.get(k):
            measured[k] = counts[k]

    impact: dict[str, Any] = {
        "lost_successful_payments": counts["lost_payments"],
        "lost_amount_minor": {c: v for c, v in sorted(amounts["lost_amount"].items())},
        "failed_attempts_delta": (counts["attempts_actual"] - counts["success_actual"]) - (counts["attempts_cf"] - counts["success_cf"]),
        "extra_refunds": counts["extra_refunds"],
        "extra_refund_amount_minor": {c: v for c, v in sorted(amounts["extra_refund_amount"].items())},
        "duplicate_charges": counts["duplicate_charges"],
        "duplicate_amount_minor": {c: v for c, v in sorted(amounts["duplicate_amount"].items())},
        "fraudulent_succeeded": counts["injected_succeeded"],
        "fraudulent_amount_minor": {c: v for c, v in sorted(amounts["injected_amount"].items())},
        "lost_renewals": counts["lost_renewals"],
        "lost_renewal_amount_minor": {c: v for c, v in sorted(amounts["lost_renewal_amount"].items())},
        "method": "simulator counterfactual: identical random draws evaluated with and without the effect",
    }

    affected_payments = counts["lost_payments"] + counts["extra_refunds"] + counts["duplicate_charges"] + counts["injected_attempts"]
    lost_revenue: dict[str, int] = defaultdict(int)
    # Revenue lost = payments that should have succeeded + refunds forced by the incident + fraud
    # that got through. Duplicates are over-collection (customer harm), counted as affected only.
    for m in ("lost_amount", "extra_refund_amount", "injected_amount"):
        for cur, v in amounts[m].items():
            lost_revenue[cur] += v
    true_impact = {"lost_revenue_minor": dict(sorted(lost_revenue.items())),
                   "affected_payment_count": affected_payments}

    detection = None
    if truth.detection_delay_s is not None:
        # A designer's constant, kept as a lower bound of expectations; latency is measured from
        # max(start, oracle_detectable_at) (ADR-031).
        detection = {"start": truth.start, "end": truth.start + truth.detection_delay_s, "basis": "designer_constant"}
    oracle_at, oracle_method = None, None
    if is_incident:
        hourly: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for eid in truth.effect_ids:
            for h, per in tally.hourly.get(eid, {}).items():
                for k, v in per.items():
                    hourly[h][k] += v
        oracle_at, oracle_method = oracle_detectability(truth.primary_metric, hourly, truth.start, organic or {})
        if oracle_at is not None and truth.end is not None and oracle_at > truth.end:
            oracle_at = None  # not distinguishable within the incident window

    return GroundTruth(
        scenario_id=scenario_id,
        scenario_kind=scenario_kind,
        record_key=truth.key,
        incident_id=incident_id,
        start=truth.start,
        end=truth.end if truth.end is not None else None,
        true_cause=truth.true_cause.value,
        root_cause={
            "cause": truth.true_cause.value,
            "locus": cohort_json(truth.locus),
            "mechanism": truth.mechanism,
            "effect_ids": list(truth.effect_ids),
        },
        expected_route=truth.expected_route.value,
        severity=truth.severity.value,
        affected_cohorts=[cohort_json(c) for c in truth.affected_cohorts],
        control_cohorts=[cohort_json(c) for c in truth.control_cohorts],
        expected_metric_effect={
            "primary_metric": truth.primary_metric,
            "direction": truth.expected_direction,
            "parameters": {eid: dict(effect_params[eid]) for eid in truth.effect_ids},
            "measured": measured,
        },
        expected_impact=impact,
        expected_detection_window=detection,
        expected_recovery=truth.expected_recovery,
        unrelated_to=sorted(unrelated_incident_ids),
        scenario_seed=scenario_seed,
        spec_hash=spec_hash,
        scenario_type=scenario_kind,
        seed=world_seed,
        is_incident=is_incident,
        onset_at=truth.start,
        end_at=truth.end,
        ramp=ramp_of(profile),
        # explicit lists since sim-1.2 (an empty `affected` is meaningful: the control day moves nothing)
        affected_metrics=list(truth.affected_metrics) if (truth.affected_metrics or truth.unchanged_metrics) else [truth.primary_metric],
        unchanged_metrics=list(truth.unchanged_metrics),
        injected_effect={eid: dict(effect_params[eid]) for eid in truth.effect_ids},
        true_impact=true_impact,
        extra={"ongoing_at_world_end": truth.end is None, "world_end": world_end},
        oracle_detectable_at=oracle_at,
        oracle_method=oracle_method,
    )


def truth_digest(records: list[GroundTruth]) -> str:
    import json

    blob = json.dumps([r.to_dict() for r in records], sort_keys=True, separators=(",", ":"))
    return short_hash(blob)
