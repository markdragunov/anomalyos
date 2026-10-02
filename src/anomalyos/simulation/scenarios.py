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
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from .ground_truth import Cohort, Mechanism, RootCause, Route, Severity, TruthSpec
from .ids import derive_seed, short_hash
from .schedule import (CARD_COUNTRY_CONTROL, GRADUAL_COHORTS, METHOD_IDS, PSP_SHORT, PSPS, RECOVERY_COHORTS, REFUND_CONTROLS,
                       params_for)
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


def _hours(seconds: int) -> str:
    return f"{seconds / HOUR:g}"


def _pct(x: float) -> int:
    return round(x * 100)


def normal_variation(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "normal_variation")
    s, e = P["start"], P["end"]
    t = TruthSpec("control_day", (), RootCause.NORMAL_VARIATION, Route.SUPPRESS, Severity.NONE, s, e, {},
                  "No injected mechanism. Organic diurnal/weekly cycles, Poisson traffic noise, daily approval jitter.",
                  (), (c(customer_country=("US", "GB", "DE", "FR", "ES", "NL", "BR", "MX", "JP")),),
                  "charge_approval_rate", "none", None, None)
    return _mk(w, "scn_normal_variation", "normal_variation", "Negative control: a normal day", Severity.NONE, (), (t,))


def psp_authorization_degradation(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "psp_authorization_degradation")
    s, e, psp = P["start"], P["end"], P["psp"]
    others = tuple(x for x in PSPS if x != psp)
    fx = Effect(f"fx_{psp}_auth", Mechanism.APPROVAL, c(psp=psp), step(s, e), P["magnitude"], PSP_OUTAGE_CODES)
    t = TruthSpec(f"{psp}_auth", (fx.effect_id,), RootCause.PSP_DEGRADATION, Route.INCIDENT,
                  Severity.HIGH, s, e, c(psp=psp),
                  f"{psp} authorization endpoint returns intermittent processing errors; all cards and local "
                  f"methods routed to {psp} authorize at ~{_pct(P['magnitude'])}% of normal. Step onset and step recovery.",
                  (c(psp=psp),), (c(psp=others),),
                  "charge_approval_rate", "down", 1 * HOUR, e)
    return _mk(w, "scn_psp_auth_degradation", "psp_authorization_degradation",
               f"{psp} authorization degradation", Severity.HIGH, (fx,), (t,))


def country_degradation(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "country_degradation")
    s, e, co = P["start"], P["end"], P["country"]
    other = CARD_COUNTRY_CONTROL[co]
    fx = Effect(f"fx_{co.lower()}_issuers", Mechanism.APPROVAL, c(customer_country=co, payment_method_type="card"), step(s, e),
                P["magnitude"], (("card_declined", "do_not_honor", .7), ("card_declined", "issuer_not_available", .3)))
    controls = [c(customer_country=other, payment_method_type="card")]
    if co == "BR":
        controls.insert(0, c(customer_country="BR", payment_method_type="pix"))
    t = TruthSpec(f"{co.lower()}_issuers", (fx.effect_id,), RootCause.ISSUER_OR_COUNTRY_DEGRADATION, Route.INCIDENT, Severity.HIGH,
                  s, e, c(customer_country=co),
                  f"Domestic card-scheme switch in {co} degraded: {co}-issued cards decline do_not_honor on every PSP"
                  + ("; pix unaffected." if co == "BR" else "."),
                  (c(customer_country=co, payment_method_type="card"),), tuple(controls),
                  "charge_approval_rate", "down", 1 * HOUR, e)
    return _mk(w, "scn_country_degradation", "country_degradation", f"{co} card issuer degradation", Severity.HIGH, (fx,), (t,))


def payment_method_degradation(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "payment_method_degradation")
    s, e, m = P["start"], P["end"], P["method"]
    fx_id, key = METHOD_IDS[m]
    fx = Effect(fx_id, Mechanism.APPROVAL, c(payment_method_type=m), step(s, e), P["magnitude"],
                (("payment_method_provider_decline", "generic_decline", .5), ("processing_error", None, .5)))
    label = {"sepa_debit": "SEPA Direct Debit mandate service", "ideal": "iDEAL scheme", "pix": "Pix switch"}[m]
    controls = (c(payment_method_type="card", psp="psp_beta"),
                c(payment_method_type="ideal" if m != "ideal" else "sepa_debit"))
    t = TruthSpec(key, (fx.effect_id,), RootCause.PAYMENT_METHOD_DEGRADATION, Route.INCIDENT, Severity.MEDIUM,
                  s, e, c(payment_method_type=m),
                  f"{label} unavailable: {m} payments fail in every country; cards on the same PSP are fine.",
                  (c(payment_method_type=m),), controls,
                  "charge_approval_rate", "down", 1 * HOUR, e)
    title = {"sepa_debit": "SEPA debit rail degradation", "ideal": "iDEAL rail degradation", "pix": "Pix rail degradation"}[m]
    return _mk(w, "scn_payment_method_degradation", "payment_method_degradation", title, Severity.MEDIUM, (fx,), (t,))


def checkout_regression(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "checkout_regression_app_version")
    platform, s, fix, end_fx = P["platform"], P["start"], P["fix"], P["end"]
    fx = Effect(f"fx_{platform}_5140", Mechanism.ABANDON, c(platform=platform, app_version="5.14.0"), step(s, end_fx), P["magnitude"],
                params={"note": "confirm call never sent by client; PI stays requires_payment_method then is canceled"})
    # Recovery: when the buggy version's share of the platform's customers falls below 5 %.
    rec = fix
    while rec < end_fx and version_share(w.releases(), platform, "5.14.0", w.adoption_mean_hours, rec) >= 0.05:
        rec += 15 * 60
    other = "ios" if platform == "android" else "android"
    name = platform.capitalize()
    t = TruthSpec(f"{platform}_5140_checkout", (fx.effect_id,), RootCause.CHECKOUT_REGRESSION, Route.INCIDENT,
                  Severity.HIGH, s, fix, c(platform=platform, app_version="5.14.0"),
                  f"{name} 5.14.0 ships a checkout bug: ~{_pct(P['magnitude'])}% of payment confirmations are never sent. Charge "
                  "approval is unaffected; PaymentIntent conversion drops and grows with rollout; hotfix 5.14.1 "
                  "released, impact decays with update adoption.",
                  (c(platform=platform, app_version="5.14.0"),),
                  (c(platform=other, app_version="5.14.0"), c(platform=platform, app_version="5.13.0"), c(platform="web")),
                  "payment_intent_conversion_rate", "down", 6 * HOUR, rec)
    return _mk(w, "scn_checkout_regression", "checkout_regression_app_version", f"{name} 5.14.0 checkout regression",
               Severity.HIGH, (fx,), (t,))


def subscription_renewal_failure(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "subscription_renewal_failure")
    s, e, psp = P["start"], P["end"], P["psp"]
    short = PSP_SHORT[psp]
    others = tuple(x for x in PSPS if x != psp)
    fx = Effect(f"fx_renewals_{short}", Mechanism.APPROVAL, c(channel="renewal", psp=psp, payment_method_type="card"),
                step(s, e), P["magnitude"], (("expired_card", "expired_card", .6), ("card_declined", "do_not_honor", .4)))
    t = TruthSpec(f"renewals_{short}", (fx.effect_id,), RootCause.RENEWAL_JOB_FAILURE, Route.INCIDENT,
                  Severity.HIGH, s, e, c(psp=psp, channel="renewal"),
                  f"{psp} network-token/account-updater service lapsed: merchant-initiated renewal charges on "
                  f"stored cards decline (expired_card). On-session {psp} checkouts are unaffected.",
                  (c(channel="renewal", psp=psp, payment_method_type="card"),),
                  (c(channel="checkout", psp=psp), c(channel="renewal", psp=others)),
                  "renewal_success_rate", "down", 12 * HOUR, e)
    return _mk(w, "scn_subscription_renewal_failure", "subscription_renewal_failure", f"Renewal failures on {psp}",
               Severity.HIGH, (fx,), (t,))


def refund_spike(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "refund_spike")
    s, e, co = P["start"], P["end"], P["country"]
    fx = Effect(f"fx_{co.lower()}_refunds", Mechanism.REFUND, c(customer_country=co), step(s, e), P["share"],
                params={"delay_min_s": 1 * HOUR, "delay_max_s": 6 * HOUR, "reason": "requested_by_customer"})
    t = TruthSpec(f"{co.lower()}_fulfillment_refunds", (fx.effect_id,), RootCause.REFUND_PROCESS_CHANGE, Route.INCIDENT,
                  Severity.MEDIUM, s, e + 6 * HOUR, c(customer_country=co),
                  f"{co} fulfilment partner fails; orders are auto-refunded within hours. Authorization is healthy.",
                  (c(customer_country=co),), (c(customer_country=REFUND_CONTROLS[co]),),
                  "refund_rate", "up", 4 * HOUR, e + 6 * HOUR)
    return _mk(w, "scn_refund_spike", "refund_spike", f"{co} refund spike", Severity.MEDIUM, (fx,), (t,))


def duplicate_charge(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "duplicate_charge")
    s, e = P["start"], P["end"]
    fx = Effect("fx_web_dupes", Mechanism.DUPLICATE, c(platform="web"), step(s, e), P["share"],
                params={"refund_share": 0.6, "refund_delay_min_s": 1 * DAY, "refund_delay_max_s": 3 * DAY})
    t = TruthSpec("web_duplicate_charges", (fx.effect_id,), RootCause.DUPLICATE_CHARGING, Route.INCIDENT,
                  Severity.CRITICAL, s, e, c(platform="web"),
                  f"API gateway deploy drops Idempotency-Key on web client retries: ~{_pct(P['share'])}% of successful web "
                  "checkouts are charged twice seconds apart (second request has no idempotency key). "
                  "Customers later get duplicate refunds.",
                  (c(platform="web"),), (c(platform=("ios", "android")),),
                  "duplicate_charge_rate", "up", 2 * HOUR, e)
    return _mk(w, "scn_duplicate_charge", "duplicate_charge", "Duplicate charges on web", Severity.CRITICAL, (fx,), (t,))


def fraud_spike(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "fraud_like_spike")
    s, e, co, psp = P["start"], P["end"], P["country"], P["psp"]
    fx = Effect("fx_card_testing", Mechanism.INJECT, c(customer_country=co, psp=psp, payment_method_type="card"),
                step(s, e), P["attempts"],
                (("card_declined", "fraudulent", .3), ("incorrect_cvc", "incorrect_cvc", .3),
                 ("card_declined", "stolen_card", .1), ("card_declined", "generic_decline", .2),
                 ("card_declined", "highest_risk_level", .1)),
                params={"success_rate": 0.08, "amount_min": 100, "amount_max": 600, "platform": "web",
                        "fraud_refund_share": 0.5})
    ctl_country = "GB" if co != "GB" else "US"
    t = TruthSpec(f"{co.lower()}_card_testing", (fx.effect_id,), RootCause.FRAUD_ATTACK, Route.INCIDENT, Severity.HIGH,
                  s, e, c(customer_country=co, psp=psp),
                  "Card-testing bot: burst of small-amount card attempts from freshly created customers on web, "
                  "very low approval, fraud-flavoured declines and elevated risk scores.",
                  (c(customer_country=co, platform="web", payment_method_type="card"),),
                  (c(customer_country=co, platform=("ios", "android")), c(customer_country=ctl_country, platform="web")),
                  "charge_attempt_volume", "up", 1 * HOUR, e)
    return _mk(w, "scn_fraud_spike", "fraud_like_spike", "Card testing burst", Severity.HIGH, (fx,), (t,))


def gradual_degradation(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "gradual_degradation")
    s, full, co, psp = P["start"], P["full"], P["country"], P["psp"]
    short = PSP_SHORT[psp]
    fx = Effect(f"fx_{co.lower()}_{short}_drift", Mechanism.APPROVAL, c(customer_country=co, psp=psp), ramp(s, full, w.end),
                P["magnitude"], (("card_declined", "do_not_honor", .5), ("card_declined", "generic_decline", .5)))
    controls = tuple(c(customer_country=cc, psp=pp) for cc, pp in GRADUAL_COHORTS[(co, psp)])
    ramp_days = (full - s) // DAY
    t = TruthSpec(f"{co.lower()}_{short}_drift", (fx.effect_id,), RootCause.PSP_DEGRADATION, Route.INCIDENT,
                  Severity.MEDIUM, s, None, c(customer_country=co, psp=psp),
                  f"{psp}'s {co} acquiring BIN loses issuer trust after a descriptor change: approval for {co} "
                  f"traffic on {psp} drifts linearly down to ~{_pct(P['magnitude'])}% of normal over {ramp_days} days, generic decline codes, "
                  "no recovery before world end.",
                  (c(customer_country=co, psp=psp),), controls,
                  "charge_approval_rate", "down", 3 * DAY, None)
    return _mk(w, "scn_gradual_degradation", "gradual_degradation", f"Slow {co}/{psp} approval drift",
               Severity.MEDIUM, (fx,), (t,))


def harmless_seasonality(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "harmless_seasonality")
    s, e, co, mag = P["start"], P["end"], P["country"], P["magnitude"]
    fx = Effect(f"fx_{co.lower()}_holiday", Mechanism.VOLUME, c(customer_country=co), step(s, e), mag)
    effect_text = (f"global approval dips only through mix shift toward lower-approval {co} traffic." if co in ("BR", "MX")
                   else "global approval is unchanged apart from a small mix shift.")
    t = TruthSpec(f"{co.lower()}_holiday_demand", (fx.effect_id,), RootCause.NORMAL_VARIATION, Route.SUPPRESS, Severity.NONE,
                  s, e, c(customer_country=co),
                  f"{co} public-holiday promotion: {co} demand x{mag:g} for {_hours(e - s)}h. Per-cohort approval unchanged; "
                  + effect_text,
                  (c(customer_country=co),), (c(customer_country="MX" if co != "MX" else "BR"),),
                  "checkout_volume", "up", None, None)
    return _mk(w, "scn_harmless_seasonality", "harmless_seasonality", f"{co} holiday demand (harmless)", Severity.NONE, (fx,), (t,))


def small_cohort_noise(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "small_cohort_noisy_anomaly")
    s, e, co, brand = P["start"], P["end"], P["country"], P["brand"]
    fx = Effect(f"fx_{co.lower()}_{brand}_blip", Mechanism.APPROVAL, c(customer_country=co, card_brand=brand), step(s, e), P["magnitude"],
                (("card_declined", "generic_decline", .6), ("card_declined", "do_not_honor", .4)))
    t = TruthSpec(f"{co.lower()}_{brand}_blip", (fx.effect_id,), RootCause.NORMAL_VARIATION, Route.SUPPRESS, Severity.NONE,
                  s, e, c(customer_country=co, card_brand=brand),
                  f"A handful of {co} {brand} attempts fail in a {_hours(e - s)}h window. Relative drop looks dramatic, "
                  "absolute volume is a few payments: not statistically or economically meaningful.",
                  (c(customer_country=co, card_brand=brand),), (c(customer_country=co, card_brand="visa" if brand != "visa" else "mastercard"),),
                  "charge_approval_rate", "down", None, None)
    return _mk(w, "scn_small_cohort_noise", "small_cohort_noisy_anomaly", "Tiny-cohort blip (noise)", Severity.NONE, (fx,), (t,))


def correlated_unrelated(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "correlated_unrelated_anomalies")
    s_camp, e_camp, co = P["camp_start"], P["camp_end"], P["camp_country"]
    s_out, e_out, m = P["out_start"], P["out_end"], P["method"]
    camp_key, out_key = f"{co.lower()}_campaign", ("ideal_outage" if m == "ideal" else "sepa_outage")
    camp = Effect(f"fx_{co.lower()}_campaign", Mechanism.VOLUME, c(customer_country=co), step(s_camp, e_camp), P["camp_magnitude"])
    out = Effect("fx_ideal" if m == "ideal" else "fx_sepa_outage", Mechanism.APPROVAL, c(payment_method_type=m), step(s_out, e_out),
                 P["out_magnitude"], (("payment_method_provider_decline", "generic_decline", .6), ("processing_error", None, .4)))
    t1 = TruthSpec(camp_key, (camp.effect_id,), RootCause.NORMAL_VARIATION, Route.SUPPRESS, Severity.NONE,
                   s_camp, e_camp, c(customer_country=co),
                   f"Planned {co} email campaign: {co} checkout volume x{P['camp_magnitude']:g}. Healthy traffic.",
                   (c(customer_country=co),), (c(customer_country="FR" if co != "FR" else "DE"),), "checkout_volume", "up", None, None,
                   unrelated_to=(out_key,))
    label, region = ("iDEAL scheme", "NL") if m == "ideal" else ("SEPA Direct Debit scheme", "EU")
    t2 = TruthSpec(out_key, (out.effect_id,), RootCause.PAYMENT_METHOD_DEGRADATION, Route.INCIDENT,
                   Severity.MEDIUM, s_out, e_out, c(payment_method_type=m),
                   f"{label} degradation ({region}) overlapping the {co} campaign in time"
                   + (" and region" if (m, co) == ("ideal", "DE") else "")
                   + "; causally unrelated to it. Must not be merged with or explained by the campaign.",
                   (c(payment_method_type=m),),
                   (c(customer_country="NL" if m == "ideal" else "DE", payment_method_type="card"), c(customer_country=co)),
                   "charge_approval_rate", "down", 1 * HOUR, e_out, unrelated_to=(camp_key,))
    title = f"{co} campaign + {'iDEAL' if m == 'ideal' else 'SEPA'} outage (co-occurring, unrelated)"
    return _mk(w, "scn_correlated_unrelated", "correlated_unrelated_anomalies", title, Severity.MEDIUM, (camp, out), (t1, t2))


def recovery_after_degradation(w: WorldConfig) -> ScenarioSpec:
    P = params_for(w, "recovery_after_degradation")
    s, rs, rec, co, psp = P["start"], P["recovery_start"], P["end"], P["country"], P["psp"]
    short = PSP_SHORT[psp]
    fx = Effect(f"fx_{co.lower()}_{short}", Mechanism.APPROVAL, c(customer_country=co, psp=psp), degrade_then_recover(s, rs, rec),
                P["magnitude"], PSP_OUTAGE_CODES)
    controls = tuple(c(customer_country=cc, psp=pp) for cc, pp in RECOVERY_COHORTS[(co, psp)])
    t = TruthSpec(f"{co.lower()}_{short}_recovery", (fx.effect_id,), RootCause.PSP_DEGRADATION, Route.INCIDENT,
                  Severity.MEDIUM, s, rec, c(customer_country=co, psp=psp),
                  f"{psp} {co} acquirer degrades to ~{_pct(P['magnitude'])}% for {_hours(rs - s)}h, then a mitigation rolls out and approval recovers "
                  f"linearly over {_hours(rec - rs)}h; recovery point is when the effect reaches zero.",
                  (c(customer_country=co, psp=psp),), controls,
                  "charge_approval_rate", "down", 1 * HOUR, rec)
    return _mk(w, "scn_recovery_after_degradation", "recovery_after_degradation", f"{co} {psp} degradation and recovery",
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
    keys = [t.key for sp in specs for t in sp.truths]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate truth key in catalog")
    if world.realism == "v2" and preset != "baseline":
        specs = specs + (benign_shocks(world, specs),)
    if world.schedule == "randomized" or world.realism == "v2":
        specs = link_overlapping(specs)
    return specs


# Cohorts for benign shocks: existing (country, psp) pairs with 10-90 attempts/hour at scale 1.0.
BENIGN_COHORTS = (("US", "psp_gamma"), ("DE", "psp_beta"), ("BR", "psp_gamma"), ("FR", "psp_beta"), ("GB", "psp_alpha"),
                  ("NL", "psp_beta"), ("GB", "psp_beta"), ("ES", "psp_gamma"), ("JP", "psp_gamma"), ("MX", "psp_gamma"))


def benign_shocks(w: WorldConfig, specs: tuple[ScenarioSpec, ...]) -> ScenarioSpec:
    """realism v2 (ADR-032): 3-6 short, modest approval dips in random cohorts that are *not* incidents.

    Strength (x0.90-0.95 for 1-3 h) is chosen to stay within the range of ordinary variation while being visibly
    larger than v1 noise. Placement keeps the 6 h gap to every short scenario and to the control day; overlap with
    long-running scenarios is allowed and recorded in unrelated_to."""
    import random as _random
    from .schedule import GAP, LONG_RUNNING
    busy = [(sp.start, sp.end) for sp in specs if sp.kind not in LONG_RUNNING]
    n = _random.Random(derive_seed(w.seed, "benign", "count")).randint(3, 6)
    effects, truths = [], []
    for i in range(n):
        for attempt in range(300):
            rng = _random.Random(derive_seed(w.seed, "benign", i, attempt))
            s = w.start + rng.randint(1, w.days - 2) * DAY + rng.randrange(0, 24) * HOUR + rng.randrange(0, 4) * 900
            e = s + rng.randrange(4, 13) * 900
            if all(e + GAP <= a or b + GAP <= s for a, b in busy):
                break
        else:
            raise ValueError(f"no placement for benign shock {i}")
        busy.append((s, e))
        co, psp = rng.choice(BENIGN_COHORTS)
        mag = round(rng.uniform(0.90, 0.95), 2)
        fx = Effect(f"fx_benign_{i + 1}", Mechanism.APPROVAL, c(customer_country=co, psp=psp), step(s, e), mag,
                    (("card_declined", "generic_decline", .6), ("card_declined", "do_not_honor", .4)))
        others = tuple(x for x in PSPS if x != psp)
        truths.append(TruthSpec(f"benign_shock_{i + 1}", (fx.effect_id,), RootCause.NORMAL_VARIATION, Route.SUPPRESS, Severity.NONE,
                                s, e, c(customer_country=co, psp=psp),
                                f"Benign shock: {co} traffic on {psp} approves at ~{_pct(mag)}% of normal for {_hours(e - s)}h "
                                "(issuer maintenance, short routing hiccup). Within ordinary variation; not an incident.",
                                (c(customer_country=co, psp=psp),), (c(customer_country=co, psp=others),),
                                "charge_approval_rate", "down", None, None))
        effects.append(fx)
    return _mk(w, "scn_benign_shocks", "benign_shock", "Benign approval shocks (not incidents)", Severity.NONE, tuple(effects), tuple(truths))


def _truth_span(sp: ScenarioSpec, t: TruthSpec) -> tuple[int, int]:
    """Time span in which the record's effects act (the truth window may end earlier, e.g. a hotfix before decay ends)."""
    fx = [e for e in sp.effects if e.effect_id in t.effect_ids]
    if fx:
        return min(e.start for e in fx), max(e.end for e in fx)
    return t.start, t.end if t.end is not None else t.start


def link_overlapping(specs: tuple[ScenarioSpec, ...]) -> tuple[ScenarioSpec, ...]:
    """Mark records of different scenarios whose effects overlap in time as causally unrelated (both directions).

    The randomized calendar lets a long-running incident overlap a short one (ADR-029); ground truth says so
    explicitly, which also gives the incident engine a ready negative test for merging."""
    spans = {t.key: _truth_span(sp, t) for sp in specs for t in sp.truths}
    owner = {t.key: sp.scenario_id for sp in specs for t in sp.truths}
    out = []
    for sp in specs:
        truths = []
        for t in sp.truths:
            a0, a1 = spans[t.key]
            extra = sorted(k for k, (b0, b1) in spans.items()
                           if owner[k] != sp.scenario_id and a0 < b1 and b0 < a1)
            merged = tuple(sorted(set(t.unrelated_to) | set(extra)))
            truths.append(replace(t, unrelated_to=merged) if merged != t.unrelated_to else t)
        out.append(replace(sp, truths=tuple(truths)))
    return tuple(out)
