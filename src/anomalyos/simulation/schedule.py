"""Scenario calendar: fixed (sim-1.0 constants) or randomized per seed.

Why: with a fixed calendar every seed replays the same incident times, so "many seeds" only
repeats noise and a detector can be tuned to the schedule. In ``randomized`` mode the seed also
chooses start time, duration, affected cohort and effect strength, within explicit, reviewed
ranges (ADR-029).

Input:  ``WorldConfig`` (seed, start, days, schedule mode).
Output: ``params_for(world, kind)`` — the realized parameters of one scenario kind — and
        ``build_schedule(world)`` — all placements plus the release train. Pure functions of the
        world; no clock, no global state; random streams come from
        ``derive_seed(seed, "schedule", kind, attempt)``.
Invariants
* ``fixed`` returns exactly the sim-1.0 constants, so its output is byte-identical.
* Placement order is a fixed priority list, never dict order; a kind's draws depend only on the
  seed, the kind and the windows already placed.
* All windows lie inside the world (24 h warm-up at the start, 6 h margin at the end; the open-ended
  gradual drift ends at the world end by design).
* Short scenarios keep a gap of at least 6 h between each other; the three long-running ones
  (gradual drift, checkout regression, renewal failure) keep that gap between each other but may
  overlap short ones (such pairs are marked ``unrelated_to`` in ground truth); the control day
  overlaps nothing; the DE-campaign + outage pair overlaps by design.
* Realized values are the ones the scenario effects use, so ground truth cannot disagree with them.
Failure modes: ``ScheduleError`` if no placement is found in ``MAX_ATTEMPTS`` draws or the world is
shorter than 28 days; no silent fallback.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from .ids import derive_seed
from .world import DAY, HOUR, LOCAL_METHOD_PSP, Release, WorldConfig

MAX_ATTEMPTS = 300
GAP = 6 * HOUR
WARMUP = 24 * HOUR
END_MARGIN = 6 * HOUR
QUARTER = 900  # starts and durations are multiples of 15 minutes

PSPS = ("psp_alpha", "psp_beta", "psp_gamma")
PSP_SHORT = {"psp_alpha": "alpha", "psp_beta": "beta", "psp_gamma": "gamma"}

# Closed cohort lists. Every (country, psp) pair below exists in the world's PSP routing
# (docs/SIMULATION.md section 3) and had at least 10 attempts/hour at scale 1.0 (seed 42).
CARD_COUNTRY_CONTROL = {"BR": "MX", "MX": "BR", "GB": "FR", "FR": "GB", "ES": "FR", "JP": "MX"}
REFUND_CONTROLS = {"FR": ("DE", "ES", "NL"), "DE": ("FR", "ES", "NL"), "ES": ("FR", "DE", "NL"),
                   "GB": ("FR", "DE", "ES"), "NL": ("DE", "FR", "ES")}
METHODS = ("sepa_debit", "ideal", "pix")
METHOD_IDS = {"sepa_debit": ("fx_sepa", "sepa_rail"), "ideal": ("fx_ideal_rail", "ideal_rail"), "pix": ("fx_pix_rail", "pix_rail")}
FRAUD_COHORTS = (("US", "psp_alpha"), ("US", "psp_gamma"), ("GB", "psp_alpha"), ("DE", "psp_beta"))
GRADUAL_COHORTS = {
    ("ES", "psp_gamma"): (("ES", "psp_beta"), ("MX", "psp_gamma")),
    ("FR", "psp_beta"): (("FR", "psp_alpha"), ("DE", "psp_beta")),
    ("GB", "psp_beta"): (("GB", "psp_alpha"), ("NL", "psp_beta")),
    ("DE", "psp_beta"): (("DE", "psp_gamma"), ("FR", "psp_beta")),
    ("BR", "psp_gamma"): (("BR", "psp_alpha"), ("MX", "psp_gamma")),
}
RECOVERY_COHORTS = {
    ("GB", "psp_alpha"): (("GB", "psp_beta"), ("US", "psp_alpha")),
    ("FR", "psp_beta"): (("FR", "psp_alpha"), ("DE", "psp_beta")),
    ("DE", "psp_beta"): (("DE", "psp_gamma"), ("FR", "psp_beta")),
    ("US", "psp_gamma"): (("US", "psp_alpha"), ("BR", "psp_gamma")),
    ("BR", "psp_gamma"): (("BR", "psp_alpha"), ("MX", "psp_gamma")),
}
SMALL_COHORTS = (("JP", "amex"), ("NL", "mastercard"), ("JP", "mastercard"), ("NL", "visa"))
HARMLESS_COUNTRIES = ("BR", "MX", "DE", "FR", "GB")
CAMPAIGN_COUNTRIES = ("DE", "FR", "GB")
OUTAGE_METHODS = ("ideal", "sepa_debit")
REGRESSION_PLATFORMS = ("android", "ios")
PRICING_COUNTRIES = ("US", "DE", "GB", "FR", "BR")
# A drop in a lower-approval cohort masked by a demand boost in the high-approval US market.
MASKED_COHORTS = (("BR", "psp_gamma"), ("MX", "psp_gamma"), ("JP", "psp_gamma"), ("ES", "psp_gamma"))

# Placement priority (long-running first). Never derived from a dict's order.
PRIORITY = (
    "gradual_degradation", "checkout_regression_app_version", "subscription_renewal_failure",
    "dunning_failure", "pricing_or_plan_change",
    "normal_variation",  # needs a whole free day: placed right after the long-running scenarios
    "correlated_unrelated_anomalies", "recovery_after_degradation", "refund_spike", "duplicate_charge",
    "harmless_seasonality", "fraud_like_spike", "country_degradation", "payment_method_degradation",
    "psp_authorization_degradation", "small_cohort_noisy_anomaly",
    # sim-1.2 (phase 5)
    "simultaneous_incidents", "mix_shift_masking", "data_pipeline_issue",
    "ambiguous_signal",
)
LONG_RUNNING = frozenset({"gradual_degradation", "checkout_regression_app_version", "subscription_renewal_failure",
                          "dunning_failure", "pricing_or_plan_change"})
OPEN_ENDED = frozenset({"gradual_degradation"})
RENEWAL_GROUP = frozenset({"subscription_renewal_failure", "dunning_failure", "pricing_or_plan_change"})


class ScheduleError(RuntimeError):
    """No valid placement exists for the requested world."""


@dataclass(frozen=True)
class Placement:
    kind: str
    start: int
    end: int  # occupied span used for the gap rules (the world end for the open-ended drift)
    params: Mapping[str, Any]


@dataclass(frozen=True)
class Schedule:
    placements: Mapping[str, Placement]
    releases: tuple[Release, ...]


# ------------------------------------------------------------------------------- fixed (sim-1.0)
def _fixed_releases(w: WorldConfig) -> tuple[Release, ...]:
    s = w.start
    return (
        Release("ios", "5.12.0", s - 30 * DAY), Release("android", "5.12.0", s - 30 * DAY),
        Release("ios", "5.13.0", s + 1 * DAY + 10 * HOUR), Release("android", "5.13.0", s + 1 * DAY + 10 * HOUR),
        Release("ios", "5.14.0", s + 9 * DAY + 10 * HOUR), Release("android", "5.14.0", s + 9 * DAY + 10 * HOUR),
        Release("android", "5.14.1", s + 11 * DAY + 16 * HOUR),
        Release("ios", "5.15.0", s + 23 * DAY + 10 * HOUR), Release("android", "5.15.0", s + 23 * DAY + 10 * HOUR),
    )


def fixed_params(w: WorldConfig, kind: str) -> dict[str, Any]:
    """The sim-1.0 calendar, expressed in the same parameter schema the randomized mode produces."""
    s = w.start
    if kind == "normal_variation":
        return dict(start=s + 1 * DAY, end=s + 2 * DAY)
    if kind == "psp_authorization_degradation":
        return dict(start=s + 2 * DAY + 14 * HOUR, end=s + 2 * DAY + 17 * HOUR, psp="psp_beta", magnitude=0.65)
    if kind == "country_degradation":
        return dict(start=s + 4 * DAY + 9 * HOUR, end=s + 4 * DAY + 13 * HOUR, country="BR", magnitude=0.75)
    if kind == "payment_method_degradation":
        return dict(start=s + 6 * DAY + 18 * HOUR, end=s + 6 * DAY + 22 * HOUR, method="sepa_debit", magnitude=0.50)
    if kind == "checkout_regression_app_version":
        rel = {(r.platform, r.version): r.released for r in _fixed_releases(w)}
        return dict(platform="android", start=rel[("android", "5.14.0")], fix=rel[("android", "5.14.1")], end=s + 16 * DAY, magnitude=0.40)
    if kind == "subscription_renewal_failure":
        return dict(start=s + 12 * DAY, end=s + 14 * DAY + 12 * HOUR, psp="psp_gamma", magnitude=0.55)
    if kind == "refund_spike":
        return dict(start=s + 15 * DAY + 8 * HOUR, end=s + 15 * DAY + 20 * HOUR, country="FR", share=0.25)
    if kind == "duplicate_charge":
        return dict(start=s + 16 * DAY + 11 * HOUR, end=s + 16 * DAY + 14 * HOUR + 30 * 60, share=0.12)
    if kind == "fraud_like_spike":
        return dict(start=s + 18 * DAY + 2 * HOUR, end=s + 18 * DAY + 5 * HOUR, country="US", psp="psp_alpha", attempts=1500.0)
    if kind == "gradual_degradation":
        return dict(start=s + 19 * DAY, full=s + 26 * DAY, end=w.end, country="ES", psp="psp_gamma", magnitude=0.88)
    if kind == "harmless_seasonality":
        return dict(start=s + 20 * DAY + 12 * HOUR, end=s + 21 * DAY, country="BR", magnitude=2.2)
    if kind == "small_cohort_noisy_anomaly":
        return dict(start=s + 21 * DAY + 3 * HOUR, end=s + 21 * DAY + 6 * HOUR, country="JP", brand="amex", magnitude=0.35)
    if kind == "correlated_unrelated_anomalies":
        return dict(camp_start=s + 22 * DAY + 10 * HOUR, camp_end=s + 22 * DAY + 16 * HOUR, camp_country="DE", camp_magnitude=1.8,
                    out_start=s + 22 * DAY + 11 * HOUR, out_end=s + 22 * DAY + 15 * HOUR, method="ideal", out_magnitude=0.6)
    if kind == "recovery_after_degradation":
        return dict(start=s + 24 * DAY + 20 * HOUR, recovery_start=s + 24 * DAY + 22 * HOUR, end=s + 25 * DAY + 1 * HOUR,
                    country="GB", psp="psp_alpha", magnitude=0.70)
    if kind == "dunning_failure":
        return dict(start=s + 3 * DAY, end=s + 7 * DAY, psp="psp_alpha", magnitude=0.25)
    if kind == "pricing_or_plan_change":
        return dict(start=s + 7 * DAY + 6 * HOUR, end=s + 9 * DAY, country="DE", magnitude=0.06)
    if kind == "data_pipeline_issue":
        return dict(start=s + 3 * DAY + 14 * HOUR, end=s + 3 * DAY + 16 * HOUR, psp="psp_gamma", share=0.35)
    if kind == "simultaneous_incidents":
        return dict(start=s + 5 * DAY + 10 * HOUR, end=s + 5 * DAY + 14 * HOUR, psp="psp_alpha", psp_magnitude=0.70,
                    method="pix", method_start=s + 5 * DAY + 11 * HOUR, method_end=s + 5 * DAY + 15 * HOUR, method_magnitude=0.50)
    if kind == "mix_shift_masking":
        return dict(start=s + 8 * DAY + 12 * HOUR, end=s + 8 * DAY + 16 * HOUR, country="BR", psp="psp_gamma", magnitude=0.70,
                    boost_country="US", boost=1.8)
    if kind == "ambiguous_signal":
        return dict(start=s + 17 * DAY + 10 * HOUR, end=s + 17 * DAY + 14 * HOUR, country="FR", psp="psp_beta", magnitude=0.85, demand=1.3)
    raise KeyError(kind)


# ------------------------------------------------------------------------------- randomized
def _at(rng: random.Random, w: WorldConfig, day_lo: int, day_hi: int, hour_lo: int = 0, hour_hi: int = 24) -> int:
    """A start time: day in [day_lo, day_hi], hour in [hour_lo, hour_hi), on a 15-minute boundary."""
    day = rng.randint(day_lo, day_hi)
    hour = rng.randrange(hour_lo, hour_hi)
    return w.start + day * DAY + hour * HOUR + rng.randrange(0, 4) * QUARTER


def _dur(rng: random.Random, lo_hours: float, hi_hours: float, step: int = QUARTER) -> int:
    return rng.randrange(int(lo_hours * HOUR), int(hi_hours * HOUR) + 1, step)


def _mag(rng: random.Random, lo: float, hi: float) -> float:
    return round(rng.uniform(lo, hi), 2)


def _draw(w: WorldConfig, kind: str, rng: random.Random) -> Placement:
    if kind == "normal_variation":
        start = w.start + rng.randint(1, w.days - 2) * DAY
        return Placement(kind, start, start + DAY, dict(start=start, end=start + DAY))
    if kind == "psp_authorization_degradation":
        s = _at(rng, w, 2, 25, 6, 18); e = s + _dur(rng, 2, 5)
        return Placement(kind, s, e, dict(start=s, end=e, psp=rng.choice(PSPS), magnitude=_mag(rng, 0.55, 0.75)))
    if kind == "country_degradation":
        s = _at(rng, w, 2, 25, 6, 16); e = s + _dur(rng, 3, 6)
        return Placement(kind, s, e, dict(start=s, end=e, country=rng.choice(sorted(CARD_COUNTRY_CONTROL)), magnitude=_mag(rng, 0.65, 0.80)))
    if kind == "payment_method_degradation":
        s = _at(rng, w, 2, 25, 6, 18); e = s + _dur(rng, 3, 6)
        return Placement(kind, s, e, dict(start=s, end=e, method=rng.choice(METHODS), magnitude=_mag(rng, 0.40, 0.60)))
    if kind == "checkout_regression_app_version":
        rel = _at(rng, w, 5, 14, 8, 16); fix = rel + rng.randrange(36, 73) * HOUR  # >= 70 h after 5.13.0 whatever its jitter
        end = min(w.end, fix + int(3.5 * DAY))  # the buggy version's share is below 5 % well before this
        return Placement(kind, rel, end, dict(platform=rng.choice(REGRESSION_PLATFORMS), start=rel, fix=fix, end=end,
                                              magnitude=_mag(rng, 0.30, 0.50)))
    if kind == "subscription_renewal_failure":
        s = _at(rng, w, 3, 22, 0, 24); e = s + rng.randrange(36, 73) * HOUR
        return Placement(kind, s, e, dict(start=s, end=e, psp=rng.choice(PSPS), magnitude=_mag(rng, 0.40, 0.60)))
    if kind == "refund_spike":
        s = _at(rng, w, 2, 25, 6, 14); e = s + _dur(rng, 8, 16)
        return Placement(kind, s, e + 6 * HOUR, dict(start=s, end=e, country=rng.choice(sorted(REFUND_CONTROLS)), share=_mag(rng, 0.18, 0.35)))
    if kind == "duplicate_charge":
        s = _at(rng, w, 2, 25, 8, 16); e = s + _dur(rng, 2.5, 5)
        return Placement(kind, s, e, dict(start=s, end=e, share=_mag(rng, 0.08, 0.16)))
    if kind == "fraud_like_spike":
        s = _at(rng, w, 2, 25, 0, 5); e = s + _dur(rng, 2, 4)
        country, psp = rng.choice(FRAUD_COHORTS)
        return Placement(kind, s, e, dict(start=s, end=e, country=country, psp=psp, attempts=float(rng.randrange(1000, 2001, 50))))
    if kind == "gradual_degradation":
        s = _at(rng, w, 16, 22, 0, 24)
        start_day = (s - w.start) // DAY
        ramp_days = rng.randint(5, min(9, w.days - 1 - start_day))
        country, psp = rng.choice(sorted(GRADUAL_COHORTS))
        return Placement(kind, s, w.end, dict(start=s, full=s + ramp_days * DAY, end=w.end, country=country, psp=psp, magnitude=_mag(rng, 0.80, 0.90)))
    if kind == "harmless_seasonality":
        s = _at(rng, w, 2, 25, 6, 14); e = s + _dur(rng, 10, 16)
        return Placement(kind, s, e, dict(start=s, end=e, country=rng.choice(HARMLESS_COUNTRIES), magnitude=_mag(rng, 1.8, 2.6)))
    if kind == "small_cohort_noisy_anomaly":
        s = _at(rng, w, 2, 25, 0, 12); e = s + _dur(rng, 3, 5)
        country, brand = rng.choice(SMALL_COHORTS)
        return Placement(kind, s, e, dict(start=s, end=e, country=country, brand=brand, magnitude=_mag(rng, 0.25, 0.45)))
    if kind == "correlated_unrelated_anomalies":
        cs = _at(rng, w, 2, 25, 8, 12); ce = cs + _dur(rng, 5, 8)
        os_ = cs + rng.randrange(30, 121, 15) * 60
        oe = min(ce - HOUR, os_ + _dur(rng, 2, 4))
        if oe <= os_ + HOUR:
            raise ScheduleError("outage window too short")  # rejected by the caller's retry loop
        return Placement(kind, cs, ce, dict(camp_start=cs, camp_end=ce, camp_country=rng.choice(CAMPAIGN_COUNTRIES),
                                            camp_magnitude=_mag(rng, 1.6, 2.2), out_start=os_, out_end=oe,
                                            method=rng.choice(OUTAGE_METHODS), out_magnitude=_mag(rng, 0.45, 0.65)))
    if kind == "dunning_failure":
        s = _at(rng, w, 3, 18, 0, 24); e = s + rng.randrange(72, 121) * HOUR
        return Placement(kind, s, e, dict(start=s, end=e, psp=rng.choice(PSPS), magnitude=_mag(rng, 0.15, 0.35)))
    if kind == "pricing_or_plan_change":
        s = _at(rng, w, 3, 20, 0, 24); e = s + rng.randrange(48, 97) * HOUR
        return Placement(kind, s, e, dict(start=s, end=e, country=rng.choice(PRICING_COUNTRIES), magnitude=_mag(rng, 0.04, 0.08)))
    if kind == "data_pipeline_issue":
        s = _at(rng, w, 2, 25, 6, 20); e = s + _dur(rng, 1, 3)
        return Placement(kind, s, e + 2 * HOUR, dict(start=s, end=e, psp=rng.choice(PSPS), share=_mag(rng, 0.20, 0.50)))
    if kind == "simultaneous_incidents":
        s = _at(rng, w, 2, 25, 6, 16); e = s + _dur(rng, 3, 6)
        psp = rng.choice(PSPS)
        method = rng.choice([m for m in METHODS if LOCAL_METHOD_PSP[m] != psp])  # disjoint cohorts
        ms = s + rng.randrange(0, 121, 15) * 60
        me = min(e + 2 * HOUR, ms + _dur(rng, 2, 4))
        return Placement(kind, s, max(e, me), dict(start=s, end=e, psp=psp, psp_magnitude=_mag(rng, 0.60, 0.75), method=method,
                                                    method_start=ms, method_end=me, method_magnitude=_mag(rng, 0.40, 0.60)))
    if kind == "mix_shift_masking":
        s = _at(rng, w, 2, 25, 8, 16); e = s + _dur(rng, 3, 5)
        country, psp = rng.choice(MASKED_COHORTS)
        return Placement(kind, s, e, dict(start=s, end=e, country=country, psp=psp, magnitude=_mag(rng, 0.60, 0.75),
                                          boost_country="US", boost=_mag(rng, 1.6, 2.0)))
    if kind == "ambiguous_signal":
        s = _at(rng, w, 2, 25, 6, 18); e = s + _dur(rng, 3, 5)
        country, psp = rng.choice(sorted(RECOVERY_COHORTS))
        return Placement(kind, s, e, dict(start=s, end=e, country=country, psp=psp, magnitude=_mag(rng, 0.80, 0.90), demand=_mag(rng, 1.2, 1.4)))
    if kind == "recovery_after_degradation":
        s = _at(rng, w, 2, 25, 6, 20); rs = s + _dur(rng, 1.5, 3); rec = rs + _dur(rng, 2, 4)
        country, psp = rng.choice(sorted(RECOVERY_COHORTS))
        return Placement(kind, s, rec, dict(start=s, recovery_start=rs, end=rec, country=country, psp=psp, magnitude=_mag(rng, 0.55, 0.75)))
    raise KeyError(kind)


def _gap_ok(a: Placement, b: Placement) -> bool:
    return a.end + GAP <= b.start or b.end + GAP <= a.start


def _compatible(new: Placement, old: Placement) -> bool:
    if "normal_variation" in (new.kind, old.kind):
        return _gap_ok(new, old)  # the control day touches nothing, long scenarios included
    new_long, old_long = new.kind in LONG_RUNNING, old.kind in LONG_RUNNING
    if new_long != old_long:
        return True  # long x short overlap is allowed (marked unrelated_to in ground truth)
    if new_long and old_long:
        # sim-1.2: long scenarios are kept apart only within the group that acts on the same flow (renewals);
        # long scenarios of different flows may overlap (ADR-033).
        return _gap_ok(new, old) if {new.kind, old.kind} <= RENEWAL_GROUP else True
    return _gap_ok(new, old)


def _inside(w: WorldConfig, p: Placement) -> bool:
    last = w.end if p.kind in OPEN_ENDED else w.end - END_MARGIN
    return p.start >= w.start + WARMUP and p.end <= last


def _releases(w: WorldConfig, regression: Placement | None) -> tuple[Release, ...]:
    rng = random.Random(derive_seed(w.seed, "schedule", "releases"))
    s = w.start
    r13 = s + 1 * DAY + 10 * HOUR + rng.randrange(-24, 25) * HOUR
    r15 = s + 23 * DAY + 10 * HOUR + rng.randrange(-24, 25) * HOUR
    rel = regression.params["start"] if regression else s + 9 * DAY + 10 * HOUR
    fix = regression.params["fix"] if regression else s + 11 * DAY + 16 * HOUR
    platform = regression.params["platform"] if regression else "android"
    hotfix = (Release(platform, "5.14.1", fix),)
    return (
        Release("ios", "5.12.0", s - 30 * DAY), Release("android", "5.12.0", s - 30 * DAY),
        Release("ios", "5.13.0", r13), Release("android", "5.13.0", r13),
        Release("ios", "5.14.0", rel), Release("android", "5.14.0", rel),
        *hotfix,
        Release("ios", "5.15.0", r15), Release("android", "5.15.0", r15),
    )


@lru_cache(maxsize=256)
def build_schedule(w: WorldConfig) -> Schedule:
    if w.days < 28:
        raise ScheduleError("the randomized schedule needs days >= 28")
    placed: dict[str, Placement] = {}
    for kind in PRIORITY:
        for attempt in range(MAX_ATTEMPTS):
            rng = random.Random(derive_seed(w.seed, "schedule", kind, attempt))
            try:
                cand = _draw(w, kind, rng)
            except ScheduleError:
                continue
            if _inside(w, cand) and all(_compatible(cand, old) for old in placed.values()):
                placed[kind] = cand
                break
        else:
            raise ScheduleError(f"no placement found for {kind} (seed {w.seed}) in {MAX_ATTEMPTS} attempts")
    return Schedule(placed, _releases(w, placed.get("checkout_regression_app_version")))


def params_for(w: WorldConfig, kind: str) -> Mapping[str, Any]:
    """Realized parameters of ``kind`` for this world (sim-1.0 constants in ``fixed`` mode)."""
    if w.schedule == "fixed":
        return fixed_params(w, kind)
    return build_schedule(w).placements[kind].params


def releases_for(w: WorldConfig) -> tuple[Release, ...]:
    return _fixed_releases(w) if w.schedule == "fixed" else build_schedule(w).releases
