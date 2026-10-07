"""Correlation rules (ADR-041 D-2): does a candidate join an open incident, start a new one, or stand related?

A candidate joins an incident only if time, cohort and metric group all hold against one of its members. Cohort
nesting compares Stage 4 loci along the approved chains. A **global candidate** may join through nesting only when
exactly one incident qualifies; a **global member** of an incident never anchors a specific candidate (Gate 1 fix,
owner OK: otherwise an incident seeded by a global locus attracts unrelated candidates one by one). Ties go to the
oldest incident. Pure; Jev never creates a relationship. ADR-052 switches (``IncidentConfig``): the nesting mode
(chains, chains plus the diagnosed edges, or any pair containment), parent loci (option A) and approval drops in the
subscriptions group (option C).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from pulseos.incidents.config import (CHAIN_EDGES, CHAIN_EDGES_PLUS, METRIC_GROUPS, RENEWAL_WITH_APPROVAL,
                                     IncidentConfig)

Locus = frozenset  # of (dimension, value)


def _closure(edges) -> frozenset[tuple[frozenset[str], frozenset[str]]]:
    pairs = set(edges)
    changed = True
    while changed:
        changed = False
        for a, b in list(pairs):
            for c, d in list(pairs):
                if b == c and (a, d) not in pairs:
                    pairs.add((a, d))
                    changed = True
    return frozenset(pairs)


CHAINS = _closure(CHAIN_EDGES)
CHAINS_PLUS = _closure(CHAIN_EDGES_PLUS)


def groups(metric: str, direction: str, cfg: IncidentConfig | None = None) -> frozenset[str]:
    if cfg is not None and cfg.renewal_with_approval and (metric, direction) == ("authorization_rate", "down"):
        return RENEWAL_WITH_APPROVAL
    return METRIC_GROUPS.get((metric, direction)) or METRIC_GROUPS.get((metric, None)) or frozenset()


def groups_compatible(a: frozenset[str], b: frozenset[str]) -> frozenset[str]:
    """Shared groups; ingestion is compatible with every group (late data moves every first-look metric)."""
    if "ingestion" in a or "ingestion" in b:
        return (a | b) or frozenset({"ingestion"})
    return a & b


def nested(a: Locus, b: Locus, mode: str = "chains") -> tuple[bool, bool]:
    """(nested?, relies on a global locus?). ``mode``: the approved chains, the chains plus the ADR-052 edges, or any
    pair containment (``pairs``); conflicting values never nest."""
    if not a or not b:
        return True, True
    if a == b:
        return True, False
    small, large = (a, b) if len(a) < len(b) else (b, a)
    if not small < large:
        return False, False
    if mode == "pairs":
        return True, False
    chains = CHAINS_PLUS if mode == "chains_plus" else CHAINS
    return (frozenset(d for d, _ in small), frozenset(d for d, _ in large)) in chains, False


def nested_via_parent(a: "Member", b: "Member", mode: str) -> bool:
    """Option A (ADR-052): a specific nesting that needs at least one parent locus."""
    pairs = [(a.locus, b.parent), (a.parent, b.locus), (a.parent, b.parent)]
    return any(x and y and nested(x, y, mode) == (True, False) for x, y in pairs)


@dataclass(frozen=True)
class Member:
    candidate_id: str
    metric: str
    direction: str
    locus: Locus
    parent: Locus = frozenset()  # ADR-052 option A: the parent locus, used only when the config enables it


@dataclass(frozen=True)
class OpenGroup:
    """An open incident or digest group as the correlation rules see it."""
    group_id: str
    order: tuple[int, str]  # (created_at, id): the oldest wins ties
    start: int
    end: int
    members: tuple[Member, ...]


@dataclass(frozen=True)
class Match:
    target: str | None  # group to join, or None for a new one
    related: tuple[str, ...] = ()
    evidence: dict = field(default_factory=dict)


def _time(c_start: int, c_end: int, g: OpenGroup, gap: int) -> str | None:
    if c_start < g.end and g.start < c_end:
        return "overlap"
    if c_start <= g.end + gap and g.start <= c_end + gap:
        return "gap"
    return None


def decide(member: Member, start: int, end: int, open_groups: Iterable[OpenGroup], cfg: IncidentConfig) -> Match:
    specific, global_only, related = [], [], []
    mine = groups(member.metric, member.direction, cfg)
    for g in sorted(open_groups, key=lambda g: g.order):
        t = _time(start, end, g, cfg.gap_s)
        if t is None:
            continue
        best = None
        for m in g.members:
            shared = groups_compatible(mine, groups(m.metric, m.direction, cfg))
            if not shared:
                continue
            ok, via_global = nested(member.locus, m.locus, cfg.nesting)
            if via_global and member.locus:  # only the member is global: it cannot anchor a specific candidate
                ok = False
            ev = {"time": t, "group": sorted(shared), "member": m.candidate_id}
            if ok and not via_global:
                best = ("specific", dict(ev, cohort="nested"))
                break
            if cfg.parent_locus and nested_via_parent(member, m, cfg.nesting):
                best = ("specific", dict(ev, cohort="parent"))
                break
            if ok and best is None:
                best = ("global", dict(ev, cohort="global"))
            elif best is None:
                best = ("related", dict(ev, cohort="not_nested"))
        if best is None:
            continue
        {"specific": specific, "global": global_only, "related": related}[best[0]].append((g.group_id, best[1]))
    if specific:
        gid, ev = specific[0]
        return Match(gid, (), ev)
    if len(global_only) == 1:
        gid, ev = global_only[0]
        return Match(gid, (), dict(ev, cohort="global_unambiguous"))
    if global_only:
        return Match(None, tuple(g for g, _ in global_only), {"cohort": "global_ambiguous"})
    return Match(None, tuple(g for g, _ in related), {"cohort": "none"} if not related else {"cohort": "not_nested"})
