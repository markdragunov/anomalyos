"""Stage A — evidence narrowing (ADR-049 D-1): the anchor's pooled cohort tables, cut into chunks, recursively
selected by importance until a bounded set of leaves remains.

Input: the incident view, a runner, the configuration and an importance function (the control: summed
|contribution|; Jev: a ``noul`` per chunk, the fake while OQ-1 is open). Output: ``Narrowed`` — the surviving cohort
items (≤ ``max_leaves`` × ``leaf_size``), the number of importance calls, and the sibling values per dimension that
the check library may use. Pooling goes through ``cohorts.fetch.query_pooled`` (``metrics.compute``); this stage
does not count as tool calls (D-5) but its items count as evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from pulseos.cohorts.analysis import decompose_count, decompose_rate, evidence_id as cohort_evidence_id, z_count, z_rate
from pulseos.cohorts.config import COMBINATIONS
from pulseos.cohorts.fetch import query_pooled
from pulseos.context.readers import PSPS
from pulseos.investigation.config import InvestigationConfig
from pulseos.investigation.measure import COUNT_METRICS, version_of
from pulseos.metrics import get_metric

LAGS = (2, 3, 4, 5, 6, 7, 8)


@dataclass(frozen=True)
class Item:
    evidence_id: str
    dims: tuple[tuple[str, str], ...]
    contribution: float
    z: float | None
    support: float


@dataclass
class Narrowed:
    items: list[Item]
    importance_calls: int
    siblings: dict[str, list[str]] = field(default_factory=dict)
    chunks_seen: int = 0


Importance = Callable[[list[list[Item]]], list[float]]


def control_importance(chunks: list[list[Item]]) -> list[float]:
    return [sum(abs(i.contribution) for i in c) for c in chunks]


def _items(view, runner, db, run_id, world_start, cfg) -> tuple[list[list[Item]], dict]:
    a = view.anchor
    scope = dict(a.scope)
    kind = "count" if a.metric in COUNT_METRICS else "rate"
    allowed = set(get_metric(a.metric, version_of(a.metric)).allowed_dims)
    per_combo, tables = [], {}
    for combo in COMBINATIONS:
        if not set(combo) <= allowed or set(combo) <= set(scope):
            continue
        rows = query_pooled(runner, db, run_id, a.metric, version_of(a.metric), scope, combo, a.start, view.as_of,
                            view.grain, LAGS, world_start, kind)
        rows = list(rows.values())[:200]
        if not rows:
            continue
        tables[combo] = rows
        dec = decompose_count(rows) if kind == "count" else decompose_rate(rows)
        items = []
        for r in rows:
            if kind == "rate":
                if r.n_b < cfg.min_support or r.n_d < cfg.min_support:
                    continue
                z = z_rate(r.k_b, r.n_b, r.k_d, r.n_d)
                support = r.n_d
            else:
                z = z_count(r.k_b, max(1, len(r.ref_days)), r.k_d)
                support = r.k_d
            dims = tuple(sorted(tuple(scope.items()) + r.dims))
            items.append(Item(cohort_evidence_id(a.metric, version_of(a.metric), dims, (a.start, view.as_of)), dims,
                              dec.per_cohort[r.dims][0], z, support))
        items.sort(key=lambda i: (-abs(i.contribution), i.dims))
        per_combo.append(items)
    return per_combo, tables


def _siblings(view, tables) -> dict[str, list[str]]:
    locus, scope = dict(view.anchor.locus), dict(view.anchor.scope)
    out: dict[str, list[str]] = {}
    for d, v in sorted(locus.items()):
        if d in scope and d == "psp":
            out[d] = [p for p in PSPS if p != v]
        rows = tables.get((d,), [])
        vals = sorted(((r.n_d if r.n_d else r.k_d), x) for r in rows for (dd, x) in r.dims if dd == d and x != v)
        if vals:
            out[d] = [x for _, x in sorted(vals, key=lambda t: (-t[0], t[1]))]
    return out


def narrow(view, runner, db: str, run_id: str, world_start: int, cfg: InvestigationConfig,
           importance: Importance = control_importance) -> Narrowed:
    per_combo, tables = _items(view, runner, db, run_id, world_start, cfg)
    chunks = [items[i:i + cfg.chunk_size] for items in per_combo for i in range(0, len(items), cfg.chunk_size)]
    chunks = [c for c in chunks if c]
    calls, seen, depth = 0, len(chunks), 0
    while chunks:
        scores = importance(chunks)
        calls += 1
        ranked = [c for _, c in sorted(zip(scores, chunks), key=lambda t: (-t[0], t[1][0].dims))][: cfg.max_leaves]
        if depth >= cfg.max_depth or all(len(c) <= cfg.leaf_size for c in ranked):
            leaves = [c[: cfg.leaf_size] for c in ranked]
            break
        nxt = []
        for c in ranked:
            if len(c) <= cfg.leaf_size:
                nxt.append(c)
                continue
            step = -(-len(c) // cfg.split)
            nxt += [c[i:i + step] for i in range(0, len(c), step)]
        chunks, depth = nxt, depth + 1
        seen += len(chunks)
    else:
        leaves = []
    items = sorted({i.evidence_id: i for c in leaves for i in c}.values(), key=lambda i: (-abs(i.contribution), i.dims))
    return Narrowed(items, calls, _siblings(view, tables), seen)
