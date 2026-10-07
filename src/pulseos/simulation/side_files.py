"""Side files ``deployments.json`` and ``psp_status.json`` (sim-1.3.0, ADR-049 D-3a).

Why: the investigation agent's ``get_deployments`` and ``check_psp_status`` tools need a data source that behaves like
real change logs and status pages — sometimes revealing, often noisy.
Input: the world, the mobile release train and the scenario **effects** (mechanism, selector, profile, params).
Output: two entry lists and, separately, the honest entry ids per effect (``honest``), which the runner attaches to
ground truth as ``side_signals`` for evaluation only.
Invariants: the generator never reads ``TruthSpec`` / ``GroundTruth``, causes, routes or titles; only effects whose
author set ``params["side_signal"]`` get an honest entry; entries carry no scenario id, cause label or truth text;
randomness only from ``derive_seed(seed, "side", ...)``; the event stream is not touched (its digest is unchanged).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from .ids import derive_seed, stable_id
from .world import LOCAL_METHOD_PSP, PSPS, WorldConfig

MIN, HOUR, DAY = 60, 3600, 86_400
SERVICES = ("api_gateway", "checkout_web", "renewal_job", "dunning_service", "refund_service", "pricing_service", "ledger")
ROUTINE_PER_SERVICE_DAY = 0.3
NEAR_MISS_DEPLOY, NEAR_MISS_STATUS = 0.3, 0.2


@dataclass
class SideFiles:
    deployments: list[dict] = field(default_factory=list)
    psp_status: list[dict] = field(default_factory=list)
    honest: dict[str, list[str]] = field(default_factory=dict)  # effect id -> honest entry ids

    def honest_for(self, effect_ids: Iterable[str]) -> list[str]:
        return sorted({i for e in effect_ids for i in self.honest.get(e, [])})


def _rng(seed: int, *parts: object) -> random.Random:
    return random.Random(derive_seed(seed, "side", *parts))


def _deploy(seed: int, service: str, version: str, t: int, *key: object) -> dict:
    return {"id": stable_id("dep", seed, service, version, t, *key, length=16), "service": service, "version": version,
            "deployed_at": int(t)}


def _status(seed: int, psp: str, component: str, level: str, posted: int, resolved: int | None, *key: object) -> dict:
    return {"id": stable_id("sts", seed, psp, component, posted, *key, length=16), "psp": psp, "component": component,
            "level": level, "posted_at": int(posted), "resolved_at": None if resolved is None else int(resolved)}


def _honest(seed: int, w: WorldConfig, e, releases) -> tuple[list[dict], list[dict], str | None]:
    """Honest entries for one effect (first matching rule wins); the third value names the signal kind or None."""
    r = _rng(seed, "honest", e.effect_id)
    sel, mech, p = e.selector, e.mechanism.value, e.params
    end = e.end if e.end < w.end else None

    def deploy(service: str, prob: float):
        if r.random() >= prob:
            return [], [], service
        t = e.start - r.randint(5 * MIN, 2 * HOUR)
        return [_deploy(seed, service, f"{service}-{r.randint(100, 999)}", t, e.effect_id)], [], service

    def status(psps, component: str, levels, prob: float):
        if r.random() >= prob:
            return [], [], component
        out = []
        for psp in psps:
            posted = e.start + r.randint(20 * MIN, 90 * MIN)
            resolved = None if end is None else end + r.randint(0, 60 * MIN)
            out.append(_status(seed, psp, component, r.choice(levels), posted, resolved, e.effect_id))
        return [], out, component

    if mech == "abandon" and "platform" in sel and "app_version" in sel:
        hits = [x for x in releases if x["platform"] in sel["platform"] and x["version"] in sel["app_version"]]
        return hits, [], "release"
    if mech == "duplicate":
        return deploy("api_gateway", 0.8)
    if mech == "approval" and "renewal" in sel.get("channel", ()):
        return deploy("dunning_service" if p.get("attempt_kind") == "dunning" else "renewal_job", 0.8)
    if mech == "refund":
        return deploy("refund_service", 0.8)
    if mech == "churn":
        return deploy("pricing_service", 0.8)
    if mech == "approval" and "psp" in sel:
        return status(sel["psp"], "authorization", ("degraded", "partial_outage"), 0.7)
    methods = [m for m in sel.get("payment_method_type", ()) if m in LOCAL_METHOD_PSP]
    if mech == "approval" and methods and "psp" not in sel:
        return status(sorted({LOCAL_METHOD_PSP[m] for m in methods}), "local_methods", ("degraded", "partial_outage"), 0.5)
    if mech == "delay" and "psp" in sel:
        return status(sel["psp"], "webhooks", ("delayed",), 0.6)
    return [], [], None


def generate(w: WorldConfig, specs) -> SideFiles:
    seed = w.seed
    out = SideFiles()
    releases = [_deploy(seed, f"mobile_{rel.platform}", rel.version, rel.released, "release") | {"platform": rel.platform}
                for rel in w.releases()]
    release_ids = set()
    for d in releases:
        out.deployments.append({k: v for k, v in d.items() if k != "platform"})
        release_ids.add(d["id"])
    # routine deploys (decoys): about 0.3 per service per day
    for day in range(w.days):
        for service in SERVICES:
            r = _rng(seed, "routine", service, day)
            if r.random() < ROUTINE_PER_SERVICE_DAY:
                t = w.start + day * DAY + r.randint(0, DAY - 1)
                out.deployments.append(_deploy(seed, service, f"{service}-{r.randint(100, 999)}", t, "routine"))
    # status-page noise: weekly night maintenance and a minor degradation about every 10 days per PSP
    for psp in PSPS:
        for week in range((w.days + 6) // 7):
            r = _rng(seed, "maintenance", psp, week)
            start = w.start + week * 7 * DAY + r.randint(0, 6) * DAY + 2 * HOUR
            if start < w.end:
                out.psp_status.append(_status(seed, psp, "maintenance", "maintenance", start - DAY, start + 2 * HOUR, "maint"))
        r = _rng(seed, "minor", psp)
        t = w.start + r.randint(1, 10) * DAY
        while t < w.end:
            t0 = t + r.randint(0, DAY - 1)
            out.psp_status.append(_status(seed, psp, "performance", "degraded", t0, t0 + r.randint(30 * MIN, 120 * MIN), "minor"))
            t += r.randint(7, 13) * DAY
    # honest signals and near-miss decoys, effect by effect
    for spec in specs:
        for e in spec.effects:
            deps, stats, kind = _honest(seed, w, e, releases) if e.params.get("side_signal") else ([], [], None)
            ids = [d["id"] for d in deps] + [s_["id"] for s_ in stats]
            for d in deps:
                if d["id"] not in release_ids:
                    out.deployments.append(d)
            out.psp_status.extend(stats)
            if ids:
                out.honest[e.effect_id] = sorted(ids)
                continue
            r = _rng(seed, "near_miss", e.effect_id)
            if r.random() < NEAR_MISS_DEPLOY:
                service = r.choice([x for x in SERVICES if x != kind])
                out.deployments.append(_deploy(seed, service, f"{service}-{r.randint(100, 999)}",
                                               e.start - r.randint(5 * MIN, 2 * HOUR), "near", e.effect_id))
            if r.random() < NEAR_MISS_STATUS:
                t0 = e.start - r.randint(0, 30 * MIN)
                out.psp_status.append(_status(seed, r.choice(PSPS), "performance", "degraded", t0,
                                              t0 + r.randint(30 * MIN, 90 * MIN), "near", e.effect_id))
    out.deployments.sort(key=lambda d: (d["deployed_at"], d["id"]))
    out.psp_status.sort(key=lambda s_: (s_["posted_at"], s_["id"]))
    return out


def validate(side: SideFiles, causes: Iterable[str]) -> list[str]:
    """Every honest id exists; no entry names a cause."""
    errors = []
    ids = {d["id"] for d in side.deployments} | {s_["id"] for s_ in side.psp_status}
    for eff, hs in side.honest.items():
        errors += [f"{eff}: honest id {h} missing" for h in hs if h not in ids]
    text = repr(side.deployments) + repr(side.psp_status)
    errors += [f"cause label {c!r} appears in a side file" for c in causes if c in text]
    return errors
