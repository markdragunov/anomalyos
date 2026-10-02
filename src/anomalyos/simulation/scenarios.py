"""Scenario specs and the scenario catalog.

Why: a scenario is a *causal mechanism* injected into the baseline world (e.g. "psp_beta's
authorization path degrades to 65 % of normal between 14:00 and 17:00"), plus the designer's
declared truth about it. The engine applies mechanisms to individual payments; it never
paints metrics directly, so every anomaly is visible only through ordinary-looking events.

Input:  ``WorldConfig`` (for time anchoring and seed), preset name.
Output: tuple of immutable ``ScenarioSpec``s. Each has seed, start/end, affected dimensions
        (selectors), severity, expected metric effect, causal mechanism, ground-truth
        incident key(s) and recovery point.
Invariants
* Every spec has ≥ 1 ``TruthSpec``; every effect is referenced by exactly one truth.
* Specs are pure data derived from (world, preset) — no randomness, no wall clock.
* ``spec_hash`` fingerprints the spec so replays can be checked for identity.
Failure modes: ``build_catalog`` raises ``ValueError`` for an unknown preset or a scenario that
falls outside the world window.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from .ground_truth import Cohort, Mechanism, RootCause, Route, Severity, TruthSpec
from .ids import derive_seed, short_hash
from .world import DAY, HOUR, WorldConfig, version_share

# Dimensions a selector may constrain. Values are compared against per-payment attributes.
SELECTOR_DIMS = ("customer_country", "psp", "payment_method_type", "card_brand", "platform", "app_version", "channel")


@dataclass(frozen=True)
class Effect:
    effect_id: str
    mechanism: Mechanism
    selector: Cohort  # dim -> allowed values (AND across dims, OR within a dim)
    profile: tuple[tuple[int, float], ...]  # piecewise-linear intensity keypoints (t, 0..1)
    magnitude: float
    decline_codes: tuple[tuple[str, str | None, float], ...] = ()
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def start(self) -> int:
        return self.profile[0][0]

    @property
    def end(self) -> int:
        return self.profile[-1][0]

    def intensity(self, t: int) -> float:
        p = self.profile
        if t < p[0][0] or t >= p[-1][0]:
            return 0.0
        for (t0, s0), (t1, s1) in zip(p, p[1:]):
            if t0 <= t < t1:
                return s0 + (s1 - s0) * (t - t0) / (t1 - t0)
        return 0.0

    def matches(self, attrs: Mapping[str, str]) -> bool:
        for dim, allowed in self.selector.items():
            if attrs.get(dim) not in allowed:
                return False
        return True

    def describe(self) -> dict[str, Any]:
        return {
            "mechanism": self.mechanism.value,
            "selector": {k: sorted(v) for k, v in sorted(self.selector.items())},
            "profile": [[t, s] for t, s in self.profile],
            "magnitude": self.magnitude,
            "decline_codes": [list(x) for x in self.decline_codes],
            **{k: v for k, v in sorted(self.params.items())},
        }


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    kind: str
    title: str
    seed: int
    start: int
    end: int
    severity: Severity
    effects: tuple[Effect, ...]
    truths: tuple[TruthSpec, ...]

    def __post_init__(self) -> None:
        if not self.truths:
            raise ValueError(f"{self.scenario_id}: scenario without ground truth")
        referenced = [e for t in self.truths for e in t.effect_ids]
        declared = [e.effect_id for e in self.effects]
        if sorted(referenced) != sorted(declared):
            raise ValueError(f"{self.scenario_id}: every effect must belong to exactly one truth record")

    @property
    def spec_hash(self) -> str:
        blob = json.dumps(
            {
                "id": self.scenario_id, "kind": self.kind, "seed": self.seed, "start": self.start, "end": self.end,
                "severity": self.severity.value, "effects": {e.effect_id: e.describe() for e in self.effects},
                "truths": [repr(t) for t in self.truths],
            },
            sort_keys=True, default=str,
        )
        return short_hash(blob)


def step(start: int, end: int) -> tuple[tuple[int, float], ...]:
    return ((start, 1.0), (end, 1.0))


def ramp(start: int, full_at: int, end: int) -> tuple[tuple[int, float], ...]:
    return ((start, 0.0), (full_at, 1.0), (end, 1.0))


def degrade_then_recover(start: int, recovery_starts: int, recovered: int) -> tuple[tuple[int, float], ...]:
    return ((start, 1.0), (recovery_starts, 1.0), (recovered, 0.0))


def c(**dims: str | tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    return {k: (v,) if isinstance(v, str) else tuple(v) for k, v in dims.items()}


PSP_OUTAGE_CODES = (("processing_error", None, .6), ("card_declined", "issuer_not_available", .4))


# ---------------------------------------------------------------------------------------------
# Catalog. Offsets are relative to world start (a Monday 00:00 UTC). Each factory returns a spec.
# ---------------------------------------------------------------------------------------------

def _mk(w: WorldConfig, sid: str, kind: str, title: str, sev: Severity, effects, truths) -> ScenarioSpec:
    starts = [e.start for e in effects] + [t.start for t in truths]
    ends = [e.end for e in effects] + [t.end or w.end for t in truths]
    return ScenarioSpec(sid, kind, title, derive_seed(w.seed, "scenario", sid), min(starts), max(ends), sev,
                        tuple(effects), tuple(truths))


def normal_variation(w: WorldConfig) -> ScenarioSpec:
    s, e = w.start + 1 * DAY, w.start + 2 * DAY
    t = TruthSpec("control_day", (), RootCause.NORMAL_VARIATION, Route.SUPPRESS, Severity.NONE, s, e, {},
                  "No injected mechanism. Organic diurnal/weekly cycles, Poisson traffic noise, daily approval jitter.",
                  (), (c(customer_country=("US", "GB", "DE", "FR", "ES", "NL", "BR", "MX", "JP")),),
                  "charge_approval_rate", "none", None, None)
    return _mk(w, "scn_normal_variation", "normal_variation", "Negative control: a normal day", Severity.NONE, (), (t,))


def psp_authorization_degradation(w: WorldConfig) -> ScenarioSpec:
    s, e = w.start + 2 * DAY + 14 * HOUR, w.start + 2 * DAY + 17 * HOUR
    fx = Effect("fx_psp_beta_auth", Mechanism.APPROVAL, c(psp="psp_beta"), step(s, e), 0.65, PSP_OUTAGE_CODES)
    t = TruthSpec("psp_beta_auth", (fx.effect_id,), RootCause.PSP_DEGRADATION, Route.INCIDENT,
                  Severity.HIGH, s, e, c(psp="psp_beta"),
                  "psp_beta authorization endpoint returns intermittent processing errors; all cards and local "
                  "methods routed to psp_beta authorize at ~65% of normal. Step onset and step recovery.",
                  (c(psp="psp_beta"),), (c(psp=("psp_alpha", "psp_gamma")),),
                  "charge_approval_rate", "down", 1 * HOUR, e)
    return _mk(w, "scn_psp_auth_degradation", "psp_authorization_degradation",
               "psp_beta authorization degradation", Severity.HIGH, (fx,), (t,))


def country_degradation(w: WorldConfig) -> ScenarioSpec:
    s, e = w.start + 4 * DAY + 9 * HOUR, w.start + 4 * DAY + 13 * HOUR
    fx = Effect("fx_br_issuers", Mechanism.APPROVAL, c(customer_country="BR", payment_method_type="card"), step(s, e), 0.75,
                (("card_declined", "do_not_honor", .7), ("card_declined", "issuer_not_available", .3)))
    t = TruthSpec("br_issuers", (fx.effect_id,), RootCause.ISSUER_OR_COUNTRY_DEGRADATION, Route.INCIDENT, Severity.HIGH,
                  s, e, c(customer_country="BR"),
                  "Domestic card-scheme switch in BR degraded: BR-issued cards decline do_not_honor on every PSP; "
                  "pix unaffected.",
                  (c(customer_country="BR", payment_method_type="card"),),
                  (c(customer_country="BR", payment_method_type="pix"), c(customer_country="MX", payment_method_type="card")),
                  "charge_approval_rate", "down", 1 * HOUR, e)
    return _mk(w, "scn_country_degradation", "country_degradation", "BR card issuer degradation", Severity.HIGH, (fx,), (t,))


def payment_method_degradation(w: WorldConfig) -> ScenarioSpec:
    s, e = w.start + 6 * DAY + 18 * HOUR, w.start + 6 * DAY + 22 * HOUR
    fx = Effect("fx_sepa", Mechanism.APPROVAL, c(payment_method_type="sepa_debit"), step(s, e), 0.50,
                (("payment_method_provider_decline", "generic_decline", .5), ("processing_error", None, .5)))
    t = TruthSpec("sepa_rail", (fx.effect_id,), RootCause.PAYMENT_METHOD_DEGRADATION, Route.INCIDENT, Severity.MEDIUM,
                  s, e, c(payment_method_type="sepa_debit"),
                  "SEPA Direct Debit mandate service unavailable: sepa_debit payments fail in every country; cards "
                  "on the same PSP are fine.",
                  (c(payment_method_type="sepa_debit"),),
                  (c(payment_method_type="card", psp="psp_beta"), c(payment_method_type="ideal")),
                  "charge_approval_rate", "down", 1 * HOUR, e)
    return _mk(w, "scn_payment_method_degradation", "payment_method_degradation", "SEPA debit rail degradation",
               Severity.MEDIUM, (fx,), (t,))


def checkout_regression(w: WorldConfig) -> ScenarioSpec:
    rel = {(r.platform, r.version): r.released for r in w.releases()}
    s, fix = rel[("android", "5.14.0")], rel[("android", "5.14.1")]
    end_fx = w.start + 16 * DAY
    fx = Effect("fx_android_5140", Mechanism.ABANDON, c(platform="android", app_version="5.14.0"), step(s, end_fx), 0.40,
                params={"note": "confirm call never sent by client; PI stays requires_payment_method then is canceled"})
    # Recovery: when the buggy version's share of android customers falls below 5 %.
    rec = fix
    while rec < end_fx and version_share(w.releases(), "android", "5.14.0", w.adoption_mean_hours, rec) >= 0.05:
        rec += 15 * 60
    t = TruthSpec("android_5140_checkout", (fx.effect_id,), RootCause.CHECKOUT_REGRESSION, Route.INCIDENT,
                  Severity.HIGH, s, fix, c(platform="android", app_version="5.14.0"),
                  "Android 5.14.0 ships a checkout bug: ~40% of payment confirmations are never sent. Charge "
                  "approval is unaffected; PaymentIntent conversion drops and grows with rollout; hotfix 5.14.1 "
                  "released, impact decays with update adoption.",
                  (c(platform="android", app_version="5.14.0"),),
                  (c(platform="ios", app_version="5.14.0"), c(platform="android", app_version="5.13.0"), c(platform="web")),
                  "payment_intent_conversion_rate", "down", 6 * HOUR, rec)
    return _mk(w, "scn_checkout_regression", "checkout_regression_app_version", "Android 5.14.0 checkout regression",
               Severity.HIGH, (fx,), (t,))


def subscription_renewal_failure(w: WorldConfig) -> ScenarioSpec:
    s, e = w.start + 12 * DAY, w.start + 14 * DAY + 12 * HOUR
    fx = Effect("fx_renewals_gamma", Mechanism.APPROVAL, c(channel="renewal", psp="psp_gamma", payment_method_type="card"),
                step(s, e), 0.55, (("expired_card", "expired_card", .6), ("card_declined", "do_not_honor", .4)))
    t = TruthSpec("renewals_gamma", (fx.effect_id,), RootCause.RENEWAL_JOB_FAILURE, Route.INCIDENT,
                  Severity.HIGH, s, e, c(psp="psp_gamma", channel="renewal"),
                  "psp_gamma network-token/account-updater service lapsed: merchant-initiated renewal charges on "
                  "stored cards decline (expired_card). On-session psp_gamma checkouts are unaffected.",
                  (c(channel="renewal", psp="psp_gamma", payment_method_type="card"),),
                  (c(channel="checkout", psp="psp_gamma"), c(channel="renewal", psp=("psp_alpha", "psp_beta"))),
                  "renewal_success_rate", "down", 12 * HOUR, e)
    return _mk(w, "scn_subscription_renewal_failure", "subscription_renewal_failure", "Renewal failures on psp_gamma",
               Severity.HIGH, (fx,), (t,))


def refund_spike(w: WorldConfig) -> ScenarioSpec:
    s, e = w.start + 15 * DAY + 8 * HOUR, w.start + 15 * DAY + 20 * HOUR
    fx = Effect("fx_fr_refunds", Mechanism.REFUND, c(customer_country="FR"), step(s, e), 0.25,
                params={"delay_min_s": 1 * HOUR, "delay_max_s": 6 * HOUR, "reason": "requested_by_customer"})
    t = TruthSpec("fr_fulfillment_refunds", (fx.effect_id,), RootCause.REFUND_PROCESS_CHANGE, Route.INCIDENT,
                  Severity.MEDIUM, s, e + 6 * HOUR, c(customer_country="FR"),
                  "FR fulfilment partner fails; orders are auto-refunded within hours. Authorization is healthy.",
                  (c(customer_country="FR"),), (c(customer_country=("DE", "ES", "NL")),),
                  "refund_rate", "up", 4 * HOUR, e + 6 * HOUR)
    return _mk(w, "scn_refund_spike", "refund_spike", "FR refund spike", Severity.MEDIUM, (fx,), (t,))


def duplicate_charge(w: WorldConfig) -> ScenarioSpec:
    s, e = w.start + 16 * DAY + 11 * HOUR, w.start + 16 * DAY + 14 * HOUR + 30 * 60
    fx = Effect("fx_web_dupes", Mechanism.DUPLICATE, c(platform="web"), step(s, e), 0.12,
                params={"refund_share": 0.6, "refund_delay_min_s": 1 * DAY, "refund_delay_max_s": 3 * DAY})
    t = TruthSpec("web_duplicate_charges", (fx.effect_id,), RootCause.DUPLICATE_CHARGING, Route.INCIDENT,
                  Severity.CRITICAL, s, e, c(platform="web"),
                  "API gateway deploy drops Idempotency-Key on web client retries: ~12% of successful web "
                  "checkouts are charged twice seconds apart (second request has no idempotency key). "
                  "Customers later get duplicate refunds.",
                  (c(platform="web"),), (c(platform=("ios", "android")),),
                  "duplicate_charge_rate", "up", 2 * HOUR, e)
    return _mk(w, "scn_duplicate_charge", "duplicate_charge", "Duplicate charges on web", Severity.CRITICAL, (fx,), (t,))


def fraud_spike(w: WorldConfig) -> ScenarioSpec:
    s, e = w.start + 18 * DAY + 2 * HOUR, w.start + 18 * DAY + 5 * HOUR
    fx = Effect("fx_card_testing", Mechanism.INJECT, c(customer_country="US", psp="psp_alpha", payment_method_type="card"),
                step(s, e), 1500.0,
                (("card_declined", "fraudulent", .3), ("incorrect_cvc", "incorrect_cvc", .3),
                 ("card_declined", "stolen_card", .1), ("card_declined", "generic_decline", .2),
                 ("card_declined", "highest_risk_level", .1)),
                params={"success_rate": 0.08, "amount_min": 100, "amount_max": 600, "platform": "web",
                        "fraud_refund_share": 0.5})
    t = TruthSpec("us_card_testing", (fx.effect_id,), RootCause.FRAUD_ATTACK, Route.INCIDENT, Severity.HIGH,
                  s, e, c(customer_country="US", psp="psp_alpha"),
                  "Card-testing bot: burst of small-amount card attempts from freshly created customers on web, "
                  "very low approval, fraud-flavoured declines and elevated risk scores.",
                  (c(customer_country="US", platform="web", payment_method_type="card"),),
                  (c(customer_country="US", platform=("ios", "android")), c(customer_country="GB", platform="web")),
                  "charge_attempt_volume", "up", 1 * HOUR, e)
    return _mk(w, "scn_fraud_spike", "fraud_like_spike", "Card testing burst", Severity.HIGH, (fx,), (t,))


def gradual_degradation(w: WorldConfig) -> ScenarioSpec:
    s, full = w.start + 19 * DAY, w.start + 26 * DAY
    fx = Effect("fx_es_gamma_drift", Mechanism.APPROVAL, c(customer_country="ES", psp="psp_gamma"), ramp(s, full, w.end), 0.88,
                (("card_declined", "do_not_honor", .5), ("card_declined", "generic_decline", .5)))
    t = TruthSpec("es_gamma_drift", (fx.effect_id,), RootCause.PSP_DEGRADATION, Route.INCIDENT,
                  Severity.MEDIUM, s, None, c(customer_country="ES", psp="psp_gamma"),
                  "psp_gamma's ES acquiring BIN loses issuer trust after a descriptor change: approval for ES "
                  "traffic on psp_gamma drifts linearly down to ~88% of normal over 7 days, generic decline codes, "
                  "no recovery before world end.",
                  (c(customer_country="ES", psp="psp_gamma"),), (c(customer_country="ES", psp="psp_beta"), c(customer_country="MX", psp="psp_gamma")),
                  "charge_approval_rate", "down", 3 * DAY, None)
    return _mk(w, "scn_gradual_degradation", "gradual_degradation", "Slow ES/psp_gamma approval drift",
               Severity.MEDIUM, (fx,), (t,))


def harmless_seasonality(w: WorldConfig) -> ScenarioSpec:
    s, e = w.start + 20 * DAY + 12 * HOUR, w.start + 21 * DAY
    fx = Effect("fx_br_holiday", Mechanism.VOLUME, c(customer_country="BR"), step(s, e), 2.2)
    t = TruthSpec("br_holiday_demand", (fx.effect_id,), RootCause.NORMAL_VARIATION, Route.SUPPRESS, Severity.NONE,
                  s, e, c(customer_country="BR"),
                  "BR public-holiday promotion: BR demand x2.2 for 12h. Per-cohort approval unchanged; global "
                  "approval dips only through mix shift toward lower-approval BR traffic.",
                  (c(customer_country="BR"),), (c(customer_country="MX"),),
                  "checkout_volume", "up", None, None)
    return _mk(w, "scn_harmless_seasonality", "harmless_seasonality", "BR holiday demand (harmless)", Severity.NONE, (fx,), (t,))


def small_cohort_noise(w: WorldConfig) -> ScenarioSpec:
    s, e = w.start + 21 * DAY + 3 * HOUR, w.start + 21 * DAY + 6 * HOUR
    fx = Effect("fx_jp_amex_blip", Mechanism.APPROVAL, c(customer_country="JP", card_brand="amex"), step(s, e), 0.35,
                (("card_declined", "generic_decline", .6), ("card_declined", "do_not_honor", .4)))
    t = TruthSpec("jp_amex_blip", (fx.effect_id,), RootCause.NORMAL_VARIATION, Route.SUPPRESS, Severity.NONE,
                  s, e, c(customer_country="JP", card_brand="amex"),
                  "A handful of JP amex attempts fail in a 3h window. Relative drop looks dramatic, "
                  "absolute volume is a few payments: not statistically or economically meaningful.",
                  (c(customer_country="JP", card_brand="amex"),), (c(customer_country="JP", card_brand="visa"),),
                  "charge_approval_rate", "down", None, None)
    return _mk(w, "scn_small_cohort_noise", "small_cohort_noisy_anomaly", "Tiny-cohort blip (noise)", Severity.NONE, (fx,), (t,))


def correlated_unrelated(w: WorldConfig) -> ScenarioSpec:
    s_camp, e_camp = w.start + 22 * DAY + 10 * HOUR, w.start + 22 * DAY + 16 * HOUR
    s_ideal, e_ideal = w.start + 22 * DAY + 11 * HOUR, w.start + 22 * DAY + 15 * HOUR
    camp = Effect("fx_de_campaign", Mechanism.VOLUME, c(customer_country="DE"), step(s_camp, e_camp), 1.8)
    ideal = Effect("fx_ideal", Mechanism.APPROVAL, c(payment_method_type="ideal"), step(s_ideal, e_ideal), 0.6,
                   (("payment_method_provider_decline", "generic_decline", .6), ("processing_error", None, .4)))
    t1 = TruthSpec("de_campaign", (camp.effect_id,), RootCause.NORMAL_VARIATION, Route.SUPPRESS, Severity.NONE,
                   s_camp, e_camp, c(customer_country="DE"),
                   "Planned DE email campaign: DE checkout volume x1.8. Healthy traffic.",
                   (c(customer_country="DE"),), (c(customer_country="FR"),), "checkout_volume", "up", None, None,
                   unrelated_to=("ideal_outage",))
    t2 = TruthSpec("ideal_outage", (ideal.effect_id,), RootCause.PAYMENT_METHOD_DEGRADATION, Route.INCIDENT,
                   Severity.MEDIUM, s_ideal, e_ideal, c(payment_method_type="ideal"),
                   "iDEAL scheme degradation (NL) overlapping the DE campaign in time and region; causally "
                   "unrelated to it. Must not be merged with or explained by the campaign.",
                   (c(payment_method_type="ideal"),), (c(customer_country="NL", payment_method_type="card"), c(customer_country="DE")),
                   "charge_approval_rate", "down", 1 * HOUR, e_ideal, unrelated_to=("de_campaign",))
    return _mk(w, "scn_correlated_unrelated", "correlated_unrelated_anomalies",
               "DE campaign + iDEAL outage (co-occurring, unrelated)", Severity.MEDIUM, (camp, ideal), (t1, t2))


def recovery_after_degradation(w: WorldConfig) -> ScenarioSpec:
    s, rs, rec = w.start + 24 * DAY + 20 * HOUR, w.start + 24 * DAY + 22 * HOUR, w.start + 25 * DAY + 1 * HOUR
    fx = Effect("fx_gb_alpha", Mechanism.APPROVAL, c(customer_country="GB", psp="psp_alpha"), degrade_then_recover(s, rs, rec),
                0.70, PSP_OUTAGE_CODES)
    t = TruthSpec("gb_alpha_recovery", (fx.effect_id,), RootCause.PSP_DEGRADATION, Route.INCIDENT,
                  Severity.MEDIUM, s, rec, c(customer_country="GB", psp="psp_alpha"),
                  "psp_alpha GB acquirer degrades to ~70% for 2h, then a mitigation rolls out and approval recovers "
                  "linearly over 3h; recovery point is when the effect reaches zero.",
                  (c(customer_country="GB", psp="psp_alpha"),), (c(customer_country="GB", psp="psp_beta"), c(customer_country="US", psp="psp_alpha")),
                  "charge_approval_rate", "down", 1 * HOUR, rec)
    return _mk(w, "scn_recovery_after_degradation", "recovery_after_degradation", "GB psp_alpha degradation and recovery",
               Severity.MEDIUM, (fx,), (t,))


FACTORIES = {
    "normal_variation": normal_variation,
    "psp_authorization_degradation": psp_authorization_degradation,
    "country_degradation": country_degradation,
    "payment_method_degradation": payment_method_degradation,
    "checkout_regression_app_version": checkout_regression,
    "subscription_renewal_failure": subscription_renewal_failure,
    "refund_spike": refund_spike,
    "duplicate_charge": duplicate_charge,
    "fraud_like_spike": fraud_spike,
    "gradual_degradation": gradual_degradation,
    "harmless_seasonality": harmless_seasonality,
    "small_cohort_noisy_anomaly": small_cohort_noise,
    "correlated_unrelated_anomalies": correlated_unrelated,
    "recovery_after_degradation": recovery_after_degradation,
}

PRESETS = {
    # The four that jointly exercise incident, slow drift, noise and recovery.
    "core": ("normal_variation", "psp_authorization_degradation", "gradual_degradation", "recovery_after_degradation"),
    "full": tuple(FACTORIES),
    "baseline": (),
}


def build_catalog(world: WorldConfig, preset: str = "full") -> tuple[ScenarioSpec, ...]:
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
    specs = tuple(FACTORIES[k](world) for k in PRESETS[preset])
    for sp in specs:
        if sp.start < world.start or sp.start >= world.end:
            raise ValueError(f"{sp.scenario_id} starts outside the world window; use days >= 28")
        for fx in sp.effects:
            if fx.end > world.end:
                raise ValueError(f"{fx.effect_id} ends after the world window")
    ids = [fx.effect_id for sp in specs for fx in sp.effects]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate effect_id in catalog")
    return specs
