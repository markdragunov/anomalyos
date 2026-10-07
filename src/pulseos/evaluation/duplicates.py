"""Why duplicate incidents exist (scope-dimension task, Phase 0 diagnosis). Evaluation only; no product change.

Input: the Stage 6 event stream (``incidents.pipeline`` result), refolded by ``RecordingEngine`` — the product engine
plus a record of which incidents were open and which candidates seeded each new incident; the Stage 6 scoring rows
(each incident's main record, ``evaluation.incidents``); ground-truth records.
Output: one row per duplicate — an engine incident whose main record already has an earlier incident — with the first
correlation rule that kept its seed candidates out of that earlier incident (``REASONS``), plus Stage 4 locus outcomes
per covered record. Only evaluation code reads ground truth (INV-015).

Reasons, checked in this order against the earlier incident as it was open when the duplicate was created:

* ``not_open`` — the earlier incident was not open (closed or promoted);
* ``time`` — no time overlap and the gap exceeds ``G``;
* ``group`` — no member shares a metric group (e.g. subscriptions vs payments);
* ``global`` — the loci nest only through a global locus, which the ADR-041 amendment blocks or finds ambiguous;
* ``chain`` — the loci nest as cohorts (one's pairs contain the other's) but no approved chain links their dimensions;
* ``scope`` — they nest only after each side drops the dimensions of its own Stage 3 scope;
* ``disjoint`` — different cohorts even then (conflicting values or unrelated dimensions);
* ``joinable`` — the rules would have joined it (a precedence effect, e.g. an older incident won the tie).
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

from pulseos.evaluation.cohorts import _canonical, localization
from pulseos.evaluation.decisions import status
from pulseos.incidents import correlate
from pulseos.incidents.engine import CandidateInfo, Engine

REASONS = ("not_open", "time", "group", "global", "chain", "scope", "disjoint", "joinable")
_CLOSENESS = ("joinable", "global", "chain", "scope", "disjoint")  # per-member outcome, closest to joining first
_SEED_ORDER = _CLOSENESS + ("group", "time", "not_open")  # across seeds of one duplicate, closest to joining first


class RecordingEngine(Engine):
    """The product engine, unchanged in behaviour, recording the open incidents and seed candidates at each creation."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.created: dict[str, dict] = {}  # incident id -> {"time", "open": [OpenGroup], "seeds": [CandidateInfo]}

    def _new_incident(self, seed: CandidateInfo, t: int):
        snapshot = self._open("incident", t)
        g = super()._new_incident(seed, t)
        self.created[g.gid] = {"time": t, "open": snapshot, "seeds": []}
        return g

    def _link(self, g, info: CandidateInfo, t: int, kind: str, evidence: dict) -> None:
        rec = self.created.get(g.gid)
        if rec is not None and rec["time"] == t and kind in ("create", "promote"):
            rec["seeds"].append(info)
        super()._link(g, info, t, kind, evidence)


def _pairs_nested(a: frozenset, b: frozenset) -> bool:
    return bool(a) and bool(b) and (a <= b or b <= a)


def _strip(locus: frozenset, scope: frozenset) -> frozenset:
    dims = {d for d, _ in scope}
    return frozenset(x for x in locus if x[0] not in dims)


def member_outcome(c: CandidateInfo, m_locus: frozenset, m_scope: frozenset) -> str:
    """How close one candidate came to joining through one member that shares a metric group."""
    ok, via_global = correlate.nested(c.locus, m_locus)
    if ok and not via_global:
        return "joinable"
    if ok:
        return "global"
    if _pairs_nested(c.locus, m_locus):
        return "chain"
    if _pairs_nested(_strip(c.locus, c.scope), _strip(m_locus, m_scope)):
        return "scope"
    return "disjoint"


def disjoint_kind(a: frozenset, b: frozenset) -> str:
    """``conflict``: a shared dimension with different values; ``other_dims``: no shared dimension."""
    da, db = dict(a), dict(b)
    return "conflict" if any(da[d] != db[d] for d in set(da) & set(db)) else "other_dims"


def closest(c: CandidateInfo, earlier: correlate.OpenGroup | None, scopes: Mapping[str, frozenset], t: int,
            gap_s: int) -> tuple[str, correlate.Member | None]:
    """The reason and the member of the earlier incident that came closest (None before the cohort rules)."""
    if earlier is None:
        return "not_open", None
    if correlate._time(c.start, t, earlier, gap_s) is None:
        return "time", None
    mine = correlate.groups(c.metric, c.direction)
    shared = [m for m in earlier.members if correlate.groups_compatible(mine, correlate.groups(m.metric, m.direction))]
    if not shared:
        return "group", None
    ranked = sorted(((_CLOSENESS.index(member_outcome(c, m.locus, scopes.get(m.candidate_id, frozenset()))), m.candidate_id, m)
                     for m in shared), key=lambda x: x[:2])
    return _CLOSENESS[ranked[0][0]], ranked[0][2]


def reason(c: CandidateInfo, earlier: correlate.OpenGroup | None, scopes: Mapping[str, frozenset], t: int,
           gap_s: int) -> str:
    return closest(c, earlier, scopes, t, gap_s)[0]


def classify(engine: RecordingEngine, rows: Iterable[Mapping[str, Any]], records: Iterable[Mapping[str, Any]],
             gap_s: int) -> list[dict[str, Any]]:
    """One row per duplicate; the seed closest to joining decides the reason (a promotion seeds several).
    ``counted`` marks the duplicates the Stage 6 metric counts (incident records with ``oracle_detectable_at``)."""
    by_key = {r["record_key"]: r for r in records}
    scopes = {cid: i.scope for cid, i in engine.info.items()}
    first: dict[str, str] = {}
    out = []
    for row in sorted((r for r in rows if r["main"]), key=lambda r: (engine.created[r["incident_id"]]["time"],
                                                                      r["incident_id"])):
        key, iid = row["main"], row["incident_id"]
        if key not in first:
            first[key] = iid
            continue
        rec = engine.created[iid]
        earlier = next((g for g in rec["open"] if g.group_id == first[key]), None)
        found = sorted(((_SEED_ORDER.index(r), i, s, m) for i, s in enumerate(rec["seeds"])
                        for r, m in [closest(s, earlier, scopes, rec["time"], gap_s)]), key=lambda x: x[:2])
        best = _SEED_ORDER[found[0][0]] if found else "not_open"
        seed = found[0][2] if found else None
        member = found[0][3] if found else None
        out.append({"incident_id": iid, "earlier": first[key], "record": key,
                    "scenario_kind": by_key[key]["scenario_kind"], "expected_route": by_key[key]["expected_route"],
                    "counted": by_key[key]["expected_route"] == "incident"
                    and by_key[key].get("oracle_detectable_at") is not None,
                    "reason": best, "seed_metric": seed.metric if seed else None,
                    "seed_locus": sorted(seed.locus) if seed else [], "seed_scope": sorted(seed.scope) if seed else [],
                    "member_metric": member.metric if member else None,
                    "member_locus": sorted(member.locus) if member else [],
                    "member_scope": sorted(scopes.get(member.candidate_id, frozenset())) if member else [],
                    "disjoint_kind": disjoint_kind(seed.locus, member.locus) if best == "disjoint" else None})
    return out


def first_look_loci(events: Iterable[Any], candidates: Iterable[Mapping[str, Any]],
                    records: Iterable[Mapping[str, Any]], world_end: int) -> list[dict[str, Any]]:
    """Per incident record: the first matched candidate's first-look locus against the root-cause locus; for an
    over-specific locus, whether the extra dimensions all come from the Stage 3 scope."""
    recs = [dict(r) for r in records]
    cands = {c["anomaly_id"]: dict(c) for c in candidates}
    first: dict[str, tuple] = {}
    for ev in sorted((e for e in events if e.kind == "detected"), key=lambda e: (e.time, e.candidate_id)):
        rec = status(cands[ev.info.candidate_id], recs, world_end)[1]
        if rec is None or rec["expected_route"] != "incident" or rec["record_key"] in first:
            continue
        first[rec["record_key"]] = (ev.info, rec)
    out = []
    for key, (info, rec) in sorted(first.items()):
        truth = _canonical((rec.get("root_cause") or {}).get("locus") or {}, info.metric)
        ours = _canonical({d: v for d, v in info.locus}, info.metric)
        result = localization([(d, v[0]) for d, v in ours.items()], truth)
        extra = sorted(set(ours) - set(truth)) if result == "over_specific" else []
        scope_dims = {d for d, _ in info.scope}
        out.append({"record": key, "scenario_kind": rec["scenario_kind"], "metric": info.metric, "result": result,
                    "extra_dims": extra, "extra_from_scope": bool(extra) and set(extra) <= scope_dims})
    return out


def summarize(dups: list[dict[str, Any]]) -> dict[str, Any]:
    dups = [d for d in dups if d["counted"]]
    by_kind: dict[str, Counter] = {}
    for d in dups:
        by_kind.setdefault(d["scenario_kind"], Counter())[d["reason"]] += 1
    return {"total": len(dups), "by_reason": dict(Counter(d["reason"] for d in dups)),
            "by_kind": {k: dict(v) for k, v in sorted(by_kind.items())}}
