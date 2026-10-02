"""Static description of the synthetic merchant: markets, rails, PSP routing, traffic shape.

Why: the baseline ("normal") world must have realistic structure so that injected scenarios
are hard to tell from organic behaviour — diurnal/weekly cycles in local time, a country mix
with different approval levels (so mix shifts move global metrics), several PSPs with
country-dependent routing, local payment methods, app release rollouts and monthly renewals.

Input:  ``WorldConfig`` (seed, UTC window, scale). Everything else is fixed reference data here.
Output: pure functions used by the engine: traffic rate, base approval, routing, app version.
Invariants
* Pure and deterministic: no randomness here except through explicitly passed uniforms /
  derived seeds. No wall clock; the window is part of the config.
* PSP, bank and brand names are generic (``psp_alpha`` …) — no real provider is modelled.
Failure modes: ``WorldConfig.__post_init__`` raises ``ValueError`` on an invalid window/scale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

HOUR = 3600
DAY = 86400


def ts(iso: str) -> int:
    """'2026-08-03T00:00:00Z' → Unix seconds (UTC). Only used for config literals."""
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


def iso(t: int) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class Market:
    country: str
    currency: str
    weight: float  # share of checkout traffic
    tz_offset: int  # hours from UTC (fixed, no DST — documented simplification)
    base_approval: float  # first-attempt card approval before PSP/brand modifiers
    median_amount: int  # minor units
    method_mix: tuple[tuple[str, float], ...]
    brand_mix: tuple[tuple[str, float], ...]
    card_routing: tuple[tuple[str, float], ...]  # PSP split for card traffic
    sub_price: int  # monthly subscription price, minor units


MARKETS: tuple[Market, ...] = (
    Market("US", "usd", 0.30, -5, 0.930, 4500, (("card", 1.0),),
           (("visa", .55), ("mastercard", .30), ("amex", .15)), (("psp_alpha", .6), ("psp_gamma", .4)), 1299),
    Market("GB", "gbp", 0.12, 0, 0.920, 3800, (("card", 1.0),),
           (("visa", .60), ("mastercard", .30), ("amex", .10)), (("psp_alpha", .5), ("psp_beta", .5)), 999),
    Market("DE", "eur", 0.12, 1, 0.900, 4200, (("card", .55), ("sepa_debit", .45)),
           (("visa", .50), ("mastercard", .45), ("amex", .05)), (("psp_beta", .6), ("psp_gamma", .4)), 1199),
    Market("FR", "eur", 0.09, 1, 0.910, 4000, (("card", .85), ("sepa_debit", .15)),
           (("visa", .55), ("mastercard", .40), ("amex", .05)), (("psp_beta", .7), ("psp_alpha", .3)), 1199),
    Market("ES", "eur", 0.07, 1, 0.890, 3500, (("card", 1.0),),
           (("visa", .60), ("mastercard", .35), ("amex", .05)), (("psp_gamma", .7), ("psp_beta", .3)), 1199),
    Market("NL", "eur", 0.06, 1, 0.920, 3900, (("card", .35), ("ideal", .60), ("sepa_debit", .05)),
           (("visa", .50), ("mastercard", .45), ("amex", .05)), (("psp_beta", 1.0),), 1199),
    Market("BR", "brl", 0.12, -3, 0.820, 15000, (("card", .60), ("pix", .40)),
           (("visa", .50), ("mastercard", .45), ("amex", .05)), (("psp_alpha", .5), ("psp_gamma", .5)), 3490),
    Market("MX", "mxn", 0.06, -6, 0.800, 60000, (("card", 1.0),),
           (("visa", .55), ("mastercard", .40), ("amex", .05)), (("psp_alpha", .4), ("psp_gamma", .6)), 19900),
    Market("JP", "jpy", 0.06, 9, 0.880, 5000, (("card", 1.0),),
           (("visa", .45), ("mastercard", .20), ("jcb", .25), ("amex", .10)), (("psp_beta", .3), ("psp_gamma", .7)), 1500),
)
MARKET_BY_COUNTRY = {m.country: m for m in MARKETS}
EU = frozenset({"DE", "FR", "ES", "NL"})

LOCAL_METHOD_PSP = {"sepa_debit": "psp_beta", "ideal": "psp_beta", "pix": "psp_gamma"}
METHOD_APPROVAL = {"sepa_debit": 0.970, "ideal": 0.960, "pix": 0.950}
PSP_MODIFIER = {"psp_alpha": 1.000, "psp_beta": 0.990, "psp_gamma": 0.985}
BRAND_MODIFIER = {"visa": 1.0, "mastercard": 1.0, "amex": 0.97, "jcb": 0.99}
FUNDING_MIX = (("credit", .55), ("debit", .40), ("prepaid", .05))
FUNDING_MODIFIER = {"credit": 1.0, "debit": 0.985, "prepaid": 0.90}
PLATFORM_MIX = (("web", .50), ("ios", .30), ("android", .20))
PSPS = ("psp_alpha", "psp_beta", "psp_gamma")
PAYMENT_METHOD_TYPES = ("card", "sepa_debit", "ideal", "pix")
CARD_BRANDS = ("visa", "mastercard", "amex", "jcb")

# Local-time shape of checkout demand; mean over the day is 1.0.
_DIURNAL_RAW = (0.30, 0.20, 0.15, 0.12, 0.12, 0.18, 0.35, 0.60, 0.85, 1.00, 1.10, 1.20,
                1.30, 1.25, 1.20, 1.20, 1.25, 1.35, 1.55, 1.75, 1.85, 1.65, 1.15, 0.65)
DIURNAL = tuple(v * 24 / sum(_DIURNAL_RAW) for v in _DIURNAL_RAW)
WEEKLY = (0.96, 0.97, 0.98, 1.00, 1.04, 1.03, 1.02)  # Mon..Sun, local

# Organic decline mix (failure_code, decline_code, weight).
ORGANIC_CARD_DECLINES = (
    ("card_declined", "insufficient_funds", .35), ("card_declined", "generic_decline", .25),
    ("card_declined", "do_not_honor", .20), ("expired_card", "expired_card", .07),
    ("incorrect_cvc", "incorrect_cvc", .05), ("card_declined", "fraudulent", .03),
    ("processing_error", None, .05),
)
ORGANIC_LOCAL_DECLINES = (
    ("payment_method_provider_decline", "generic_decline", .70), ("processing_error", None, .30),
)


@dataclass(frozen=True, slots=True)
class Release:
    platform: str
    version: str
    released: int


@dataclass(frozen=True)
class WorldConfig:
    seed: int = 42
    start: int = field(default_factory=lambda: ts("2026-08-03T00:00:00Z"))  # a Monday
    days: int = 28
    scale: float = 1.0
    base_checkouts_per_hour: float = 450.0  # at scale 1.0, all markets
    customers: int = 60_000  # at scale 1.0
    subscriber_share: float = 0.5
    adoption_mean_hours: float = 20.0  # mean app-update lag per customer
    organic_abandon: float = 0.03
    organic_3ds_abandon: float = 0.04
    three_ds_share_eu: float = 0.25
    retry_probability: float = 0.35
    retry_approval_factor: float = 0.55
    renewal_approval_factor: float = 0.97
    dunning_approval_factor: float = 0.50
    organic_refund_rate: float = 0.03
    organic_duplicate_rate: float = 0.0005
    daily_noise_sd: float = 0.004
    schedule: str = "fixed"  # "fixed" = sim-1.0 calendar; "randomized" = per-seed calendar (ADR-029)

    def __post_init__(self) -> None:
        if self.schedule not in ("fixed", "randomized"):
            raise ValueError("schedule must be 'fixed' or 'randomized'")
        if self.days < 1 or self.days > 366:
            raise ValueError("days must be in [1, 366]")
        if not (0 < self.scale <= 20):
            raise ValueError("scale must be in (0, 20]")
        if self.start % HOUR:
            raise ValueError("start must be aligned to a whole UTC hour")

    @property
    def end(self) -> int:
        return self.start + self.days * DAY

    @property
    def hours(self) -> int:
        return self.days * 24

    def releases(self) -> tuple[Release, ...]:
        """Mobile release train: the sim-1.0 train in ``fixed`` mode; in ``randomized`` mode the bug release
        follows the checkout-regression scenario and the others are jittered (``schedule.py``)."""
        from .schedule import releases_for  # local import: schedule.py imports this module

        return releases_for(self)

def pick(weighted: tuple[tuple[str, float], ...], u: float) -> str:
    """Weighted choice from a uniform in [0, 1). Weights need not sum to 1."""
    total = 0.0
    for _, w in weighted:
        total += w
    x = u * total
    acc = 0.0
    for v, w in weighted:
        acc += w
        if x < acc:
            return v
    return weighted[-1][0]


def pick3(weighted: tuple[tuple[str, str | None, float], ...], u: float) -> tuple[str, str | None]:
    total = sum(w for _, _, w in weighted)
    x = u * total
    acc = 0.0
    for a, b, w in weighted:
        acc += w
        if x < acc:
            return a, b
    return weighted[-1][0], weighted[-1][1]


def demand_multiplier(market: Market, t: int) -> float:
    """Local diurnal × weekly × payday shape at UTC time ``t``."""
    local = t + market.tz_offset * HOUR
    hour = (local // HOUR) % 24
    weekday = ((local // DAY) + 3) % 7  # 1970-01-01 was a Thursday → Mon=0
    dom = datetime.fromtimestamp(local, tz=timezone.utc).day
    payday = 1.08 if dom in (1, 2, 15, 16) else 1.0
    return DIURNAL[hour] * WEEKLY[weekday] * payday


def month_end_factor(market: Market, t: int) -> float:
    """Slightly more insufficient-funds declines in the last days of the month."""
    local = datetime.fromtimestamp(t + market.tz_offset * HOUR, tz=timezone.utc)
    return 0.99 if local.day >= 27 else 1.0


def route(country: str, method: str, u: float) -> str:
    if method != "card":
        return LOCAL_METHOD_PSP[method]
    return pick(MARKET_BY_COUNTRY[country].card_routing, u)


def app_version(releases: tuple[Release, ...], platform: str, lag_seconds: int, t: int) -> str:
    """Version a customer runs at ``t``: newest release they have picked up after their lag."""
    if platform == "web":
        return "web"
    current = "unknown"
    for r in releases:
        if r.platform == platform and r.released + lag_seconds <= t:
            current = r.version
    return current


def adoption_lag_seconds(mean_hours: float, u: float) -> int:
    return int(-math.log(1.0 - u) * mean_hours * HOUR)


def version_share(releases: tuple[Release, ...], platform: str, version: str, mean_hours: float, t: int) -> float:
    """Analytic share of a platform's customers on ``version`` at ``t`` (exponential lag)."""
    rel = sorted((r for r in releases if r.platform == platform), key=lambda r: r.released)
    for i, r in enumerate(rel):
        if r.version != version:
            continue
        m = mean_hours * HOUR
        took = 1 - math.exp(-max(0, t - r.released) / m) if t >= r.released else 0.0
        if i + 1 < len(rel):
            nxt = rel[i + 1].released
            left = 1 - math.exp(-max(0, t - nxt) / m) if t >= nxt else 0.0
        else:
            left = 0.0
        return max(0.0, took - left)
    return 0.0
