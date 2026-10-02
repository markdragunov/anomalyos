"""Deterministic event generator: baseline world + injected scenario mechanisms.

Why: produce a realistic, reproducible Stripe-shaped event stream (1M+ events at scale 1.0)
together with an answer key that is derived from the *mechanism*, not from the output.

Input:  ``WorldConfig`` and a tuple of ``ScenarioSpec`` (see ``scenarios.build_catalog``).
Output: ``Simulation.run()`` yields ``Event``s in non-decreasing ``created`` order;
        afterwards ``Simulation.ground_truth()`` returns one ``GroundTruth`` per truth record.

How
* Time advances hour by hour. Each hour has its own RNG (seeded from world seed + hour), each
  scenario effect has its own RNG stream per hour, each subscription renewal its own RNG.
  Every PaymentIntent consumes a fixed-length vector of uniforms, so a scenario only perturbs
  payments inside its cohort/window; everything else is byte-identical to a baseline run.
* Each payment's lifecycle (3DS, attempts, retries, cancel, refunds, duplicates, dunning) is
  resolved eagerly from its uniforms; resulting events go to a time-ordered heap and are flushed
  once the clock passes them. Events at or after world end are dropped.
* Counterfactual accounting: the same uniforms are evaluated with and without each matching
  effect (leave-one-out when several overlap). Differences feed ``EffectTally`` → ground truth.

Invariants
* No wall clock, no unseeded randomness, no ``hash()``; ordering ties broken by emission order.
* Events never contain scenario/incident identifiers.
* Object snapshots are frozen at emission (see ``models``).
Failure modes: ``ValueError`` for unsupported effect selectors; otherwise pure computation.
"""

from __future__ import annotations

import heapq
import math
import random
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from statistics import NormalDist
from typing import Any, Iterator

from .ground_truth import EffectTally, GroundTruth, Mechanism, Route, build_ground_truth
from .ids import derive_seed, stable_id
from .models import (
    ZERO_DECIMAL_CURRENCIES, Charge, Customer, Event, Invoice, PaymentIntent, PaymentMethod, Refund,
    Subscription, canonical_json,
)
from .scenarios import Effect, ScenarioSpec
from .world import (
    BRAND_MODIFIER, CARD_BRANDS, DAY, EU, FUNDING_MIX, FUNDING_MODIFIER, HOUR, MARKET_BY_COUNTRY, MARKETS,
    METHOD_APPROVAL, ORGANIC_CARD_DECLINES, ORGANIC_LOCAL_DECLINES, PLATFORM_MIX, PSP_MODIFIER, Market,
    WorldConfig, adoption_lag_seconds, app_version, demand_multiplier, month_end_factor, pick, pick3, route,
)

_N = NormalDist()

# Indices into a checkout's uniform vector (fixed length → stream isolation).
U_CUST, U_AMT, U_SEC, U_ABANDON, U_3DS, U_A1, U_C1, U_RETRY, U_RDELAY, U_A2, U_C2, U_REF, U_REFDELAY, U_DUP, U_DUPREF, U_RISK = range(16)
K_CHECKOUT = 16
K_RENEWAL = 10


@dataclass(frozen=True, slots=True)
class Shopper:
    customer: Customer
    pm: PaymentMethod
    platform: str
    lag_s: int
    psp: str  # sticky routing decision for this customer's payment method


@dataclass(slots=True)
class Attempt:
    t: int
    ok: bool
    failure_code: str | None
    decline_code: str | None
    caused_by: str | None  # effect id if the decline is attributable to an effect


@dataclass(slots=True)
class Outcome:
    abandoned: bool
    abandon_by: str | None
    attempts: list[Attempt]
    final_ok: bool
    refund_at: int | None
    refund_reason: str | None
    refund_by: str | None
    duplicate: bool
    duplicate_by: str | None


def poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam < 30:
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= rng.random()
            if p <= limit:
                return k
            k += 1
    return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))


def price(median: int, currency: str, u: float) -> int:
    z = _N.inv_cdf(min(max(u, 1e-9), 1 - 1e-9))
    v = median * math.exp(0.8 * z)
    if currency in ZERO_DECIMAL_CURRENCIES:
        return max(100, int(round(v / 10)) * 10)
    return max(99, int(round(v / 100)) * 100 - 1)


def _risk(u: float, boost: float = 0.0) -> dict[str, Any]:
    # sim-1.1: effect-caused declines get no hidden boost; only the attack stream passes risk_boost (review 4.2).
    score = min(99, int(u ** 3 * 60 + boost))
    level = "normal" if score < 65 else ("elevated" if score < 75 else "highest")
    return {"risk_score": score, "risk_level": level}


def _outcome(ok: bool, failure_code: str | None, decline_code: str | None, risk: dict[str, Any]) -> dict[str, Any]:
    if ok:
        return {"network_status": "approved_by_network", "type": "authorized", "reason": None,
                "seller_message": "Payment complete.", **risk}
    if decline_code == "highest_risk_level":
        return {"network_status": "not_sent_to_network", "type": "blocked", "reason": "highest_risk_level",
                "seller_message": "Payment blocked by risk rules.", **risk}
    if failure_code == "processing_error":
        return {"network_status": "not_sent_to_network", "type": "invalid", "reason": None,
                "seller_message": "Processing error.", **risk}
    return {"network_status": "declined_by_network", "type": "issuer_declined", "reason": decline_code,
            "seller_message": "The bank declined the payment.", **risk}


class Simulation:
    def __init__(self, world: WorldConfig, specs: tuple[ScenarioSpec, ...]):
        self.w = world
        self.seed = world.seed
        self.specs = specs
        self.releases = world.releases()
        self.tally = EffectTally()
        self.stats: Counter[str] = Counter()
        self._heap: list[tuple[int, int, Event]] = []
        self._seq = 0
        self._noise: dict[tuple[str, str, int], float] = {}
        self.world_hourly: dict[int, tuple[int, int]] = {}  # hour -> (charge attempts, successes), for reference truth
        self._done = False

        fx = [e for s in specs for e in s.effects]
        self.by_mech: dict[Mechanism, list[Effect]] = {m: [e for e in fx if e.mechanism is m] for m in Mechanism}
        for e in self.by_mech[Mechanism.VOLUME]:
            if set(e.selector) != {"customer_country"}:
                raise ValueError(f"{e.effect_id}: volume effects may only select on customer_country")
        self.per_pi_fx = (self.by_mech[Mechanism.APPROVAL] + self.by_mech[Mechanism.ABANDON]
                          + self.by_mech[Mechanism.REFUND] + self.by_mech[Mechanism.DUPLICATE])

        self.pools: dict[str, list[Shopper]] = {}
        self.shoppers: dict[str, Shopper] = {}
        self.subs: dict[str, Subscription] = {}
        self.renewals_due: dict[int, list[str]] = {}

    # ------------------------------------------------------------------ emission
    def _emit(self, t: int, etype: str, obj: Any, prev: dict[str, Any] | None = None, *,
              request_id: str | None = None, idem: str | None = None, n: int = 0) -> None:
        ev = Event(
            id=stable_id("evt", self.seed, obj.id, etype, t, n),
            created=t, type=etype, object_type=obj.object_type, object_id=obj.id,
            data_json=canonical_json(obj.to_dict()),
            previous_attributes_json=canonical_json(prev) if prev is not None else None,
            request_id=request_id, idempotency_key=idem,
        )
        if self.by_mech[Mechanism.DELAY]:
            ev = self._maybe_delay(ev, obj, t)
        self._seq += 1
        heapq.heappush(self._heap, (t, self._seq, ev))

    def _maybe_delay(self, ev: Event, obj: Any, t: int) -> Event:
        """data_pipeline_issue (sim-1.2): a share of the cohort's events reaches the store late. Payments are unaffected;
        only `delivered_at` moves. Own random stream per event id, so nothing else changes."""
        md = getattr(obj, "metadata", None) or {}
        for e in self.by_mech[Mechanism.DELAY]:
            s = e.intensity(t)
            if s <= 0 or not e.matches(md):
                continue
            rng = random.Random(derive_seed(self.seed, "delay", ev.id))
            if rng.random() < s * e.magnitude:
                lo, hi = e.params["delay_min_s"], e.params["delay_max_s"]
                self.tally.hour = t // HOUR
                self.tally.add(e.effect_id, "delayed_events")
                self.tally.hour = None
                return replace(ev, delivered_at=t + lo + int(rng.random() * (hi - lo)))
        return ev

    def _flush(self, before: int) -> Iterator[Event]:
        h = self._heap
        while h and h[0][0] < before:
            ev = heapq.heappop(h)[2]
            if ev.created >= self.w.end:
                continue
            self.stats[ev.type] += 1
            if ev.type in ("charge.succeeded", "charge.failed"):
                n, k = self.world_hourly.get((ev.created // HOUR), (0, 0))
                self.world_hourly[ev.created // HOUR] = (n + 1, k + (ev.type == "charge.succeeded"))
            yield ev

    def _req(self, *parts: object) -> str:
        return stable_id("req", self.seed, *parts, length=14)

    def _idem(self, *parts: object) -> str:
        return str(uuid.UUID(int=derive_seed(self.seed, "idem", *parts) << 64 | derive_seed(self.seed, "idem2", *parts), version=4))

    # ------------------------------------------------------------------ population
    def _populate(self) -> None:
        w, rng = self.w, random.Random(derive_seed(self.seed, "population"))
        n_total = max(len(MARKETS) * 20, int(w.customers * w.scale))
        for m in MARKETS:
            pool: list[Shopper] = []
            for i in range(max(20, int(round(n_total * m.weight)))):
                u = [rng.random() for _ in range(12)]
                subscriber = u[0] < w.subscriber_share
                span = 80 * DAY if subscriber else 119 * DAY
                created = w.start - 120 * DAY + int(u[1] * span)
                cus = Customer(stable_id("cus", self.seed, m.country, i, length=14), created, m.country,
                               "business" if u[2] < 0.1 else "consumer")
                method = pick(m.method_mix, u[3])
                brand = pick(tuple((b, wt) for b, wt in m.brand_mix), u[4]) if method == "card" else None
                funding = pick(FUNDING_MIX, u[5]) if method == "card" else None
                pm = PaymentMethod(stable_id("pm", self.seed, cus.id, length=24), created + 60, cus.id, method,
                                   m.country, brand, funding, stable_id("fp", self.seed, cus.id, length=16)[3:])
                sh = Shopper(cus, pm, pick(PLATFORM_MIX, u[6]), adoption_lag_seconds(w.adoption_mean_hours, u[7]),
                             route(m.country, method, u[8]))
                pool.append(sh)
                self.shoppers[cus.id] = sh
                self._emit(created, "customer.created", cus, request_id=self._req(cus.id))
                self._emit(created + 60, "payment_method.attached", pm, request_id=self._req(pm.id))
                if subscriber:
                    pe = w.start + int(u[9] * 30 * DAY)
                    ps = pe - 30 * DAY
                    s_created = created + HOUR + int(u[10] * (ps - created - HOUR))
                    sub = Subscription(stable_id("sub", self.seed, cus.id, length=24), s_created, cus.id, "active",
                                       f"price_monthly_{m.currency}", m.sub_price, m.currency, ps, pe, pm.id,
                                       {"plan": "monthly", "customer_country": m.country})
                    self.subs[sub.id] = sub
                    self._emit(s_created, "customer.subscription.created", sub, request_id=self._req(sub.id))
                    self._due(sub.id, pe)
            self.pools[m.country] = pool

    def _due(self, sub_id: str, at: int) -> None:
        if at < self.w.end:
            self.renewals_due.setdefault((at - self.w.start) // HOUR, []).append(sub_id)

    # ------------------------------------------------------------------ probabilities
    def _daily_noise(self, psp: str, country: str, t: int) -> float:
        key = (psp, country, (t - self.w.start) // DAY)
        v = self._noise.get(key)
        if v is None:
            v = 1.0 + random.Random(derive_seed(self.seed, "noise", *key)).gauss(0.0, self.w.daily_noise_sd)
            self._noise[key] = v
        return v

    def _v2_factor(self, psp: str, m, t: int) -> float:
        """realism v2 (ADR-032): overdispersion from a (psp, country, hour) random effect plus a weekend dip.
        Its own random streams, so v1 runs consume exactly the draws they always did."""
        key = ("v2h", psp, m.country, (t - self.w.start) // HOUR)
        v = self._noise.get(key)
        if v is None:
            v = 1.0 + random.Random(derive_seed(self.seed, "noise_v2_hour", psp, m.country, key[3])).gauss(0.0, self.w.hourly_noise_sd)
            self._noise[key] = v
        local_weekday = ((t + m.tz_offset * HOUR) // DAY + 3) % 7  # 1970-01-01 was a Thursday; 5, 6 = Sat, Sun
        return v * (self.w.weekend_approval_factor if local_weekday >= 5 else 1.0)

    def _base_p(self, sh: Shopper, t: int, kind: str) -> float:
        m = MARKET_BY_COUNTRY[sh.customer.country]
        pm = sh.pm
        if pm.type == "card":
            p = m.base_approval * BRAND_MODIFIER[pm.card_brand] * FUNDING_MODIFIER[pm.card_funding]
        else:
            p = METHOD_APPROVAL[pm.type]
        p *= PSP_MODIFIER[sh.psp] * self._daily_noise(sh.psp, m.country, t) * month_end_factor(m, t)
        if self.w.realism == "v2":
            p *= self._v2_factor(sh.psp, m, t)
        if kind == "retry":
            p *= self.w.retry_approval_factor
        elif kind == "renewal":
            p *= self.w.renewal_approval_factor
        elif kind == "dunning":
            p *= self.w.renewal_approval_factor * self.w.dunning_approval_factor
        return min(p, 0.995)

    @staticmethod
    def _approval(active: list[Effect], t: int, kind: str | None = None) -> tuple[float, Effect | None]:
        mult, worst, worst_m = 1.0, None, 1.0
        for e in active:
            if e.mechanism is not Mechanism.APPROVAL:
                continue
            if e.params.get("attempt_kind") not in (None, kind):
                continue  # e.g. a dunning-only effect does not touch first renewal attempts
            s = e.intensity(t)
            if s <= 0:
                continue
            m = 1.0 - s * (1.0 - e.magnitude)
            mult *= m
            if m < worst_m:
                worst, worst_m = e, m
        return mult, worst

    def _attempt(self, sh: Shopper, t: int, kind: str, u_ok: float, u_code: float, active: list[Effect]) -> Attempt:
        base = self._base_p(sh, t, kind)
        mult, worst = self._approval(active, t, kind)
        p = base * mult
        if u_ok < p:
            return Attempt(t, True, None, None, None)
        if worst is not None and u_ok < base and worst.decline_codes:
            fc, dc = pick3(worst.decline_codes, u_code)
            return Attempt(t, False, fc, dc, worst.effect_id)
        fc, dc = pick3(ORGANIC_CARD_DECLINES if sh.pm.type == "card" else ORGANIC_LOCAL_DECLINES, u_code)
        return Attempt(t, False, fc, dc, None)

    def _resolve_checkout(self, sh: Shopper, t: int, three_ds: bool, u: list[float], active: list[Effect]) -> Outcome:
        w = self.w
        organic_ab = w.organic_abandon + (w.organic_3ds_abandon if three_ds else 0.0)
        ab = organic_ab
        ab_by = None
        for e in active:
            if e.mechanism is Mechanism.ABANDON and e.intensity(t) > 0:
                ab += e.magnitude * e.intensity(t)
                ab_by = e.effect_id
        if u[U_ABANDON] < ab:
            return Outcome(True, ab_by if u[U_ABANDON] >= organic_ab else None, [], False, None, None, None, False, None)
        t1 = t + (35 if three_ds else 2)
        attempts = [self._attempt(sh, t1, "first", u[U_A1], u[U_C1], active)]
        if not attempts[0].ok and u[U_RETRY] < w.retry_probability:
            t2 = t1 + 60 + int(u[U_RDELAY] * 1740)
            attempts.append(self._attempt(sh, t2, "retry", u[U_A2], u[U_C2], active))
        ok = attempts[-1].ok
        refund_at = refund_reason = refund_by = None
        dup = False
        dup_by = None
        if ok:
            ts_ok = attempts[-1].t
            rp, rp_org = w.organic_refund_rate, w.organic_refund_rate
            eff_ref = None
            for e in active:
                if e.mechanism is Mechanism.REFUND and e.intensity(ts_ok) > 0:
                    rp += e.magnitude * e.intensity(ts_ok)
                    eff_ref = e
            if u[U_REF] < rp:
                if u[U_REF] >= rp_org and eff_ref is not None:
                    lo, hi = eff_ref.params["delay_min_s"], eff_ref.params["delay_max_s"]
                    refund_at = ts_ok + lo + int(u[U_REFDELAY] * (hi - lo))
                    refund_reason, refund_by = eff_ref.params["reason"], eff_ref.effect_id
                else:
                    refund_at = ts_ok + DAY + int(u[U_REFDELAY] * 9 * DAY)
                    refund_reason = "requested_by_customer"
            dp = w.organic_duplicate_rate
            for e in active:
                if e.mechanism is Mechanism.DUPLICATE and e.intensity(ts_ok) > 0:
                    dp += e.magnitude * e.intensity(ts_ok)
                    dup_by = e.effect_id
            if u[U_DUP] < dp:
                dup = True
                if u[U_DUP] < w.organic_duplicate_rate:
                    dup_by = None
            else:
                dup_by = None
        return Outcome(False, None, attempts, ok, refund_at, refund_reason, refund_by, dup, dup_by)

    # ------------------------------------------------------------------ flows
    def _attrs(self, sh: Shopper, t: int, channel: str) -> dict[str, str]:
        return {
            "customer_country": sh.customer.country, "psp": sh.psp, "payment_method_type": sh.pm.type,
            "card_brand": sh.pm.card_brand or "", "platform": sh.platform if channel == "checkout" else "server",
            "app_version": app_version(self.releases, sh.platform, sh.lag_s, t) if channel == "checkout" else "",
            "channel": channel,
        }

    @staticmethod
    def _meta(a: dict[str, str]) -> dict[str, str]:
        return {"channel": a["channel"], "psp": a["psp"], "customer_country": a["customer_country"],
                "platform": a["platform"], "app_version": a["app_version"]}

    def _checkout(self, key: tuple, m: Market, lo: int, hi: int, u: list[float], extra_of: str | None) -> None:
        pool = self.pools[m.country]
        sh = pool[min(len(pool) - 1, int(len(pool) * u[U_CUST] ** 1.7))]
        t = lo + int(u[U_SEC] * (hi - lo))
        amount = price(m.median_amount, m.currency, u[U_AMT])
        attrs = self._attrs(sh, t, "checkout")
        three_ds = sh.pm.type == "card" and m.country in EU and u[U_3DS] < self.w.three_ds_share_eu

        cands = [e for e in self.per_pi_fx if e.start <= t + 2 * HOUR and e.end > t and e.matches(attrs)]
        actual = self._resolve_checkout(sh, t, three_ds, u, cands)
        for e in self.by_mech[Mechanism.INJECT]:  # organic baseline of the attacked cohort, for the oracle
            if e.intensity(t) > 0 and e.matches(attrs):
                self.tally.hour = t // HOUR
                self.tally.add(e.effect_id, "cohort_attempts", len(actual.attempts))
                self.tally.hour = None
        if cands or self.by_mech[Mechanism.VOLUME]:
            self._tally_checkout(sh, t, amount, m.currency, three_ds, u, cands, actual, attrs, extra_of)
        self._emit_checkout(key, sh, t, amount, m.currency, attrs, three_ds, u, actual)

    def _tally_checkout(self, sh, t, amount, cur, three_ds, u, cands, actual, attrs, extra_of) -> None:
        T = self.tally
        T.hour = t // HOUR
        try:
            self._tally_checkout_at(sh, t, amount, cur, three_ds, u, cands, actual, attrs, extra_of)
        finally:
            T.hour = None

    def _tally_checkout_at(self, sh, t, amount, cur, three_ds, u, cands, actual, attrs, extra_of) -> None:
        T = self.tally
        n_ok = sum(a.ok for a in actual.attempts)
        for e in self.by_mech[Mechanism.VOLUME]:
            if e.intensity(t) <= 0:
                continue
            T.add(e.effect_id, "global_attempts_actual", len(actual.attempts))
            T.add(e.effect_id, "global_success_actual", n_ok)
            if extra_of is None:
                T.add(e.effect_id, "global_attempts_cf", len(actual.attempts))
                T.add(e.effect_id, "global_success_cf", n_ok)
            if e.matches(attrs):
                T.add(e.effect_id, "pi_actual")
                T.add(e.effect_id, "pi_succeeded_actual", int(actual.final_ok))
                T.add(e.effect_id, "attempts_actual", len(actual.attempts))
                T.add(e.effect_id, "success_actual", n_ok)
                if extra_of == e.effect_id:
                    T.add(e.effect_id, "extra_payment_intents")
                else:
                    T.add(e.effect_id, "pi_cf")
                    T.add(e.effect_id, "pi_succeeded_cf", int(actual.final_ok))
                    T.add(e.effect_id, "attempts_cf", len(actual.attempts))
                    T.add(e.effect_id, "success_cf", n_ok)
        if not cands:
            return
        for e in cands:
            others = [x for x in cands if x is not e]
            cf = self._resolve_checkout(sh, t, three_ds, u, others)
            eid = e.effect_id
            if e.intensity(t) > 0:
                T.add(eid, "pi_actual")
                T.add(eid, "pi_cf")
                T.add(eid, "pi_succeeded_actual", int(actual.final_ok))
                T.add(eid, "pi_succeeded_cf", int(cf.final_ok))
                T.add(eid, "attempts_actual", len(actual.attempts))
                T.add(eid, "attempts_cf", len(cf.attempts))
                T.add(eid, "success_actual", n_ok)
                T.add(eid, "success_cf", sum(a.ok for a in cf.attempts))
            if cf.final_ok and not actual.final_ok:
                T.add(eid, "lost_payments")
                T.add_amount(eid, "lost_amount", cur, amount)
            if actual.refund_by == eid and cf.refund_at is None:
                T.add(eid, "extra_refunds")
                T.add_amount(eid, "extra_refund_amount", cur, amount)
            if actual.duplicate_by == eid and not cf.duplicate:
                T.add(eid, "duplicate_charges")
                T.add_amount(eid, "duplicate_amount", cur, amount)

    def _emit_checkout(self, key, sh: Shopper, t: int, amount: int, cur: str, attrs, three_ds: bool,
                       u: list[float], o: Outcome, *, duplicate_of: str | None = None, risk_boost: float = 0.0) -> None:
        meta = self._meta(attrs)
        pi = PaymentIntent(stable_id("pi", self.seed, *key), t, amount, cur, sh.customer.id, sh.pm.id,
                           "requires_confirmation", meta)
        idem = None if duplicate_of else self._idem(pi.id)
        self._emit(t, "payment_intent.created", pi, request_id=self._req(pi.id), idem=idem)
        if three_ds:
            prev = pi.status
            pi = replace(pi, status="requires_action")
            self._emit(t + 2, "payment_intent.requires_action", pi, {"status": prev})
        if o.abandoned:
            prev = pi.status
            pi = replace(pi, status="canceled", cancellation_reason="abandoned", canceled_at=t + HOUR)
            self._emit(t + HOUR, "payment_intent.canceled", pi, {"status": prev, "cancellation_reason": None, "canceled_at": None})
            return
        risk_u = u[U_RISK]
        ch = None
        for n, a in enumerate(o.attempts):
            ch = Charge(stable_id("ch", self.seed, pi.id, n), a.t, amount, cur, sh.customer.id, pi.id, sh.pm.id,
                        sh.pm.type, "succeeded" if a.ok else "failed",
                        _outcome(a.ok, a.failure_code, a.decline_code, _risk(risk_u, risk_boost)),
                        meta, sh.pm.card_brand, sh.pm.country, sh.pm.card_funding, a.failure_code)
            self._emit(a.t, "charge.succeeded" if a.ok else "charge.failed", ch,
                       request_id=self._req(pi.id, "confirm", n) if n == 0 and not duplicate_of else None)
            prev = {"status": pi.status, "latest_charge": pi.latest_charge}
            if a.ok:
                pi = replace(pi, status="succeeded", latest_charge=ch.id, amount_received=amount, last_payment_error=None)
                prev["amount_received"] = 0
                self._emit(a.t, "payment_intent.succeeded", pi, prev)
            else:
                err = {"type": "card_error", "code": a.failure_code, "decline_code": a.decline_code, "charge": ch.id}
                prev["last_payment_error"] = pi.last_payment_error and dict(pi.last_payment_error)
                pi = replace(pi, status="requires_payment_method", latest_charge=ch.id, last_payment_error=err)
                self._emit(a.t, "payment_intent.payment_failed", pi, prev, n=n)
        if not o.final_ok:
            tc = o.attempts[-1].t + HOUR
            prev = pi.status
            pi = replace(pi, status="canceled", cancellation_reason="abandoned", canceled_at=tc)
            self._emit(tc, "payment_intent.canceled", pi, {"status": prev, "cancellation_reason": None, "canceled_at": None})
            return
        assert ch is not None
        if o.refund_at is not None:
            self._refund(ch, o.refund_at, o.refund_reason or "requested_by_customer", meta)
        if o.duplicate and duplicate_of is None:
            td = ch.created + 2 + int(u[U_DUPREF] * 6)
            dup_o = Outcome(False, None, [Attempt(td + 1, True, None, None, None)], True, None, None, None, False, None)
            if u[U_DUPREF] < 0.6:
                dup_o.refund_at = td + DAY + int(u[U_DUPREF] / 0.6 * 2 * DAY)
                dup_o.refund_reason = "duplicate"
            self._emit_checkout(key + ("dup",), sh, td, amount, cur, attrs, False, u, dup_o, duplicate_of=pi.id)

    def _refund(self, ch: Charge, at: int, reason: str, meta) -> None:
        re = Refund(stable_id("re", self.seed, ch.id), at, ch.amount, ch.currency, ch.id, ch.payment_intent, reason,
                    {k: meta[k] for k in ("channel", "psp", "customer_country", "platform")})
        self._emit(at, "refund.created", re)
        prev = {"amount_refunded": ch.amount_refunded, "refunded": ch.refunded}
        ch2 = replace(ch, amount_refunded=ch.amount)
        self._emit(at, "charge.refunded", ch2, prev)

    def _inject(self, e: Effect, h: int, i: int, lo: int, hi: int, rng: random.Random) -> None:
        u = [rng.random() for _ in range(8)]
        p = e.params
        country = e.selector["customer_country"][0]
        m = MARKET_BY_COUNTRY[country]
        t = lo + int(u[0] * (hi - lo))
        cus = Customer(stable_id("cus", self.seed, "inj", e.effect_id, h, i, length=14), t, country, "consumer")
        brand = "visa" if u[1] < 0.6 else "mastercard"
        pm = PaymentMethod(stable_id("pm", self.seed, cus.id, length=24), t + 1, cus.id, "card", country, brand,
                           "prepaid" if u[2] < 0.4 else "debit", stable_id("fp", self.seed, cus.id, length=16)[3:])
        sh = Shopper(cus, pm, p["platform"], 0, e.selector["psp"][0])
        self.shoppers[cus.id] = sh
        self._emit(t, "customer.created", cus, request_id=self._req(cus.id))
        self._emit(t + 1, "payment_method.attached", pm, request_id=self._req(pm.id))
        amount = p["amount_min"] + int(u[3] * (p["amount_max"] - p["amount_min"]))
        ok = u[4] < p["success_rate"]
        fc, dc = (None, None) if ok else pick3(e.decline_codes, u[5])
        att = Attempt(t + 3, ok, fc, dc, None if ok else e.effect_id)
        refund_at = t + DAY + int(u[6] * DAY) if ok and u[7] < p["fraud_refund_share"] else None
        o = Outcome(False, None, [att], ok, refund_at, "fraudulent" if refund_at else None, None, False, None)
        attrs = self._attrs(sh, t, "checkout")
        u16 = [0.0] * K_CHECKOUT
        u16[U_RISK] = u[6]
        self._emit_checkout(("inj", e.effect_id, h, i), sh, t + 2, amount, m.currency, attrs, False, u16, o,
                            risk_boost=30.0)  # bot traffic scores elevated/highest
        T = self.tally
        T.hour = t // HOUR
        T.add(e.effect_id, "injected_attempts")
        T.add(e.effect_id, "attempts_actual")
        T.add(e.effect_id, "pi_actual")
        if ok:
            T.add(e.effect_id, "injected_succeeded")
            T.add(e.effect_id, "success_actual")
            T.add(e.effect_id, "pi_succeeded_actual")
            T.add_amount(e.effect_id, "injected_amount", m.currency, amount)
        T.hour = None

    def _renewal(self, sub_id: str) -> None:
        sub = self.subs[sub_id]
        if sub.status != "active":
            return
        w, T = self.w, self.tally
        sh = self.shoppers[sub.customer]
        per_end = sub.current_period_end
        rng = random.Random(derive_seed(self.seed, "renew", sub_id, per_end))
        u = [rng.random() for _ in range(K_RENEWAL)]
        t0 = per_end + int(u[0] * 300)
        tf = t0 + HOUR
        times = [tf + 5, tf + 5 + 3 * DAY, tf + 5 + 7 * DAY]
        attrs = self._attrs(sh, tf, "renewal")
        if self.by_mech[Mechanism.CHURN] and self._churn(sub_id, sub, sh, attrs, per_end, t0):
            return
        cands = [e for e in self.by_mech[Mechanism.APPROVAL] if e.start <= times[-1] and e.end > times[0] and e.matches(attrs)]

        def resolve(active: list[Effect]) -> list[Attempt]:
            out = []
            for k, ta in enumerate(times):
                a = self._attempt(sh, ta, "renewal" if k == 0 else "dunning", u[1 + 2 * k], u[2 + 2 * k], active)
                out.append(a)
                if a.ok:
                    break
            return out

        actual = resolve(cands)
        ok = actual[-1].ok
        for e in cands:
            cf = resolve([x for x in cands if x is not e])
            eid = e.effect_id
            dunning_only = e.params.get("attempt_kind") == "dunning"
            hit = [x for x in (times[1:] if dunning_only else times[:1]) if e.intensity(x) > 0]
            T.hour = (hit[0] if hit else times[0]) // HOUR
            if hit:
                T.add(eid, "renewals")
                T.add(eid, "renewal_first_ok_actual", int(actual[0].ok))
                T.add(eid, "renewal_first_ok_cf", int(cf[0].ok))
                T.add(eid, "pi_actual")
                T.add(eid, "pi_cf")
                T.add(eid, "pi_succeeded_actual", int(ok))
                T.add(eid, "pi_succeeded_cf", int(cf[-1].ok))
                T.add(eid, "attempts_actual", len(actual))
                T.add(eid, "attempts_cf", len(cf))
                T.add(eid, "success_actual", int(ok))
                T.add(eid, "success_cf", int(cf[-1].ok))
            if cf[-1].ok and not ok:
                T.add(eid, "lost_renewals")
                T.add(eid, "lost_payments")
                T.add_amount(eid, "lost_renewal_amount", sub.currency, sub.unit_amount)
                T.add_amount(eid, "lost_amount", sub.currency, sub.unit_amount)
            T.hour = None

        meta = self._meta(attrs)
        inv = Invoice(stable_id("in", self.seed, sub_id, per_end), t0, sub.customer, sub_id, sh.customer.country,
                      "draft", sub.unit_amount, sub.currency, per_end, per_end + 30 * DAY, "subscription_cycle",
                      metadata={"customer_country": sh.customer.country})
        self._emit(t0, "invoice.created", inv)
        pi = PaymentIntent(stable_id("pi", self.seed, "inv", inv.id), tf, sub.unit_amount, sub.currency, sub.customer,
                           sh.pm.id, "requires_confirmation", meta, invoice=inv.id)
        self._emit(tf, "payment_intent.created", pi)
        inv = replace(inv, status="open", payment_intent=pi.id)
        self._emit(tf, "invoice.finalized", inv, {"status": "draft", "payment_intent": None})
        pi = replace(pi, status="processing")

        for k, a in enumerate(actual):
            ch = Charge(stable_id("ch", self.seed, pi.id, k), a.t, sub.unit_amount, sub.currency, sub.customer, pi.id,
                        sh.pm.id, sh.pm.type, "succeeded" if a.ok else "failed",
                        _outcome(a.ok, a.failure_code, a.decline_code, _risk(u[7])), meta, sh.pm.card_brand,
                        sh.pm.country, sh.pm.card_funding, a.failure_code, invoice=inv.id)
            self._emit(a.t, "charge.succeeded" if a.ok else "charge.failed", ch)
            prev_pi = {"status": pi.status, "latest_charge": pi.latest_charge}
            if a.ok:
                pi = replace(pi, status="succeeded", latest_charge=ch.id, amount_received=pi.amount, last_payment_error=None)
                self._emit(a.t, "payment_intent.succeeded", pi, prev_pi)
                prev_inv = {"status": inv.status, "amount_paid": 0, "attempt_count": inv.attempt_count,
                            "next_payment_attempt": inv.next_payment_attempt}
                inv = replace(inv, status="paid", amount_paid=inv.amount_due, attempt_count=k + 1, next_payment_attempt=None)
                self._emit(a.t + 1, "invoice.paid", inv, prev_inv)
                prev_sub = {"status": sub.status, "current_period_start": sub.current_period_start,
                            "current_period_end": sub.current_period_end, "latest_invoice": sub.latest_invoice}
                sub = replace(sub, status="active", current_period_start=per_end, current_period_end=per_end + 30 * DAY,
                              latest_invoice=inv.id)
                self._emit(a.t + 2, "customer.subscription.updated", sub, prev_sub, n=k)
                self._due(sub_id, sub.current_period_end)
            else:
                err = {"type": "card_error", "code": a.failure_code, "decline_code": a.decline_code, "charge": ch.id}
                prev_pi["last_payment_error"] = pi.last_payment_error and dict(pi.last_payment_error)
                pi = replace(pi, status="requires_payment_method", latest_charge=ch.id, last_payment_error=err)
                self._emit(a.t, "payment_intent.payment_failed", pi, prev_pi, n=k)
                nxt = times[k + 1] if k + 1 < len(times) else None
                prev_inv = {"attempt_count": inv.attempt_count, "next_payment_attempt": inv.next_payment_attempt}
                inv = replace(inv, attempt_count=k + 1, next_payment_attempt=nxt)
                self._emit(a.t + 1, "invoice.payment_failed", inv, prev_inv, n=k)
                if k == 0:
                    prev_sub = {"status": sub.status, "latest_invoice": sub.latest_invoice}
                    sub = replace(sub, status="past_due", latest_invoice=inv.id)
                    self._emit(a.t + 2, "customer.subscription.updated", sub, prev_sub, n=k)
                if nxt is None:
                    tc = a.t + 60
                    inv = replace(inv, status="uncollectible")
                    self._emit(tc, "invoice.marked_uncollectible", inv, {"status": "open"})
                    prev_s = pi.status
                    pi = replace(pi, status="canceled", cancellation_reason="failed_invoice", canceled_at=tc + 1)
                    self._emit(tc + 1, "payment_intent.canceled", pi, {"status": prev_s, "cancellation_reason": None, "canceled_at": None})
                    prev_sub = {"status": sub.status, "canceled_at": None}
                    sub = replace(sub, status="canceled", canceled_at=tc + 2)
                    self._emit(tc + 2, "customer.subscription.deleted", sub, prev_sub)
        self.subs[sub_id] = sub

    def _churn(self, sub_id: str, sub, sh, attrs, per_end: int, t0: int) -> bool:
        """pricing_or_plan_change (sim-1.2): the customer cancels at renewal instead of renewing. Own random stream."""
        active = [e for e in self.by_mech[Mechanism.CHURN] if e.intensity(t0) > 0 and e.matches(attrs)]
        if not active:
            return False
        u = random.Random(derive_seed(self.seed, "churn", sub_id, per_end)).random()
        probs = {e.effect_id: e.intensity(t0) * e.magnitude for e in active}
        total = sum(probs.values())
        churned = u < total
        T = self.tally
        T.hour = t0 // HOUR
        for e in active:
            T.add(e.effect_id, "renewal_subs")
            if churned and not u < total - probs[e.effect_id]:
                T.add(e.effect_id, "extra_cancellations")
                T.add_amount(e.effect_id, "lost_renewal_amount", sub.currency, sub.unit_amount)
        T.hour = None
        if not churned:
            return False
        prev_sub = {"status": sub.status, "canceled_at": None}
        sub = replace(sub, status="canceled", canceled_at=t0)
        self._emit(t0, "customer.subscription.deleted", sub, prev_sub)
        self.subs[sub_id] = sub
        return True

    # ------------------------------------------------------------------ main loop
    def run(self) -> Iterator[Event]:
        if self._done:
            raise RuntimeError("a Simulation instance can run only once")
        w = self.w
        self._populate()
        yield from self._flush(w.start)
        for h in range(w.hours):
            th = w.start + h * HOUR
            rng = random.Random(derive_seed(self.seed, "hour", h))
            lams: dict[str, float] = {}
            for m in MARKETS:
                lam = w.base_checkouts_per_hour * w.scale * m.weight * demand_multiplier(m, th + 1800)
                lams[m.country] = lam
                n = poisson(rng, lam)
                for i in range(n):
                    self._checkout(("co", h, m.country, i), m, th, th + HOUR, [rng.random() for _ in range(K_CHECKOUT)], None)
            for e in self.by_mech[Mechanism.VOLUME]:
                lo, hi = max(th, e.start), min(th + HOUR, e.end)
                if hi <= lo:
                    continue
                frng = random.Random(derive_seed(self.seed, "fx", e.effect_id, h))
                for country in sorted(e.selector["customer_country"]):
                    s = e.intensity(lo)
                    lam = lams[country] * s * (e.magnitude - 1.0) * (hi - lo) / HOUR
                    for i in range(poisson(frng, lam)):
                        self._checkout(("fx", e.effect_id, h, country, i), MARKET_BY_COUNTRY[country], lo, hi,
                                       [frng.random() for _ in range(K_CHECKOUT)], e.effect_id)
            for e in self.by_mech[Mechanism.INJECT]:
                lo, hi = max(th, e.start), min(th + HOUR, e.end)
                if hi <= lo:
                    continue
                frng = random.Random(derive_seed(self.seed, "fx", e.effect_id, h))
                lam = e.magnitude * w.scale * (hi - lo) / (e.end - e.start)
                for i in range(poisson(frng, lam)):
                    self._inject(e, h, i, lo, hi, frng)
            for sub_id in self.renewals_due.pop(h, []):
                self._renewal(sub_id)
            yield from self._flush(th + HOUR)
        yield from self._flush(w.end)
        self._heap.clear()
        self._done = True

    def ground_truth(self) -> list[GroundTruth]:
        if not self._done:
            raise RuntimeError("run() must be exhausted before ground truth is available")
        out: list[GroundTruth] = []
        # Truth keys are unique across the catalog (checked in build_catalog); `unrelated_to` may point at a record
        # of another scenario (randomized calendar: a long-running incident overlapping a short one).
        ref_by_key: dict[str, str] = {}
        for sp in self.specs:
            for t in sp.truths:
                ref_by_key[t.key] = (stable_id("inc", self.seed, sp.scenario_id, t.key, length=16)
                                     if t.expected_route is not Route.SUPPRESS else f"{sp.scenario_id}:{t.key}")
        for sp in self.specs:
            params = {e.effect_id: e.describe() for e in sp.effects}
            for t in sp.truths:
                unrelated = [ref_by_key.get(k) or f"{sp.scenario_id}:{k}" for k in t.unrelated_to]
                out.append(build_ground_truth(
                    world_seed=self.seed, scenario_id=sp.scenario_id, scenario_kind=sp.kind, scenario_seed=sp.seed,
                    spec_hash=sp.spec_hash, truth=t, effect_params=params, tally=self.tally,
                    unrelated_incident_ids=unrelated, world_end=self.w.end,
                    organic={"organic_refund_rate": self.w.organic_refund_rate,
                             "organic_duplicate_rate": self.w.organic_duplicate_rate,
                             # share of renewals that end canceled without any scenario (seed 42, sim-1.1: 1,055 / 29,953)
                             "organic_cancellation_rate": 0.035},
                    world_hourly=self.world_hourly,
                    profile=next((e.profile for e in sp.effects if e.effect_id in t.effect_ids), None),
                ))
        return out
