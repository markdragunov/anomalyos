"""JevState v1: the only model-facing view of a candidate (ADR-039 D-1, D-2).

Why: Jev is weak at numbers, dates and large noisy states; it sees a small set of closed-enum fields built by code.
Input: the first-look view of a Stage 3 candidate (``detection.as_of_view``), its Stage 4 ``EvidenceBundle`` (as-of
window, may be ``None``) and the candidates concurrent at detection (``detection.concurrent_at``).
Output: ``(JevState, provenance)`` — provenance maps each field to the evidence ids it was built from; Jev never sees
it. ``canonical_json`` / ``state_hash`` give a stable identity for audit and replay.
Invariants: closed enums only — no free text, numbers, timestamps, dimension values or ids; nothing from ground
truth; the bundle is the single source of every Stage 4 field and the candidate of every Stage 3 field.
Failure modes: unknown metric -> ``StateError``; serialized size above ``MAX_STATE_BYTES`` -> ``StateError`` (never
truncated). Missing Stage 4 evidence -> ``not_provided`` values.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields, is_dataclass
from typing import Any, Iterable, Mapping

from anomalyos.jev import buckets

STATE_SCHEMA_VERSION = "jev_state_v1"
MAX_STATE_BYTES = 2048
MAX_DIMS = 3
NOT_PROVIDED = "not_provided"


class StateError(ValueError):
    """The candidate cannot be expressed as a valid JevState."""


@dataclass(frozen=True)
class JevState:
    metric_family: str
    direction: str
    cadence: str
    scope_dims: tuple[str, ...]
    relative_change: str
    strength: str
    detection_rule: str
    sample_size: str
    change_type: str
    locus_kind: str
    locus_dims: tuple[str, ...]
    locus_share: str
    cohorts_moved: str
    controls: str
    related_metrics: str
    impact: str
    concurrent_alerts: str
    calendar_context: str


STATE_FIELDS = tuple(f.name for f in fields(JevState))


def _d(x: Any) -> dict:
    return asdict(x) if is_dataclass(x) else dict(x)


def _dims(pairs: Iterable) -> list[tuple[str, str]]:
    return [(str(k), str(v)) for k, v in pairs]


def _locus_share(bundle: Mapping[str, Any], locus: list[tuple[str, str]] | None) -> tuple[str, list[str]]:
    """Share of the parent's change explained by the locus cohort, from the bundle's ranked cohorts."""
    loc = bundle.get("locus") or {}
    total = (bundle.get("decomposition") or {}).get("total")
    top = bundle.get("top_cohorts") or []
    if loc.get("is_scope"):
        return "nearly_all", [bundle["bundle_id"]]
    if locus is None or not total:
        return NOT_PROVIDED, []
    want = set(locus)
    match = next((t for t in top if set(_dims(t["cohort"])) == want), None) or \
        next((t for t in top if set(_dims(t["cohort"])) >= want), None)
    if match is None:
        return NOT_PROVIDED, []
    return buckets.share(match["contribution"] / total), [match["evidence_id"]]


def _related(bundle: Mapping[str, Any], direction: str) -> tuple[str, list[str]]:
    items = [m for m in bundle.get("related_metrics") or [] if m.get("z") is not None]
    if not items:
        return NOT_PROVIDED, []
    sign = -1 if direction == "down" else 1
    moved = [sign * m["z"] >= 2.0 for m in items]
    value = "agree" if all(moved) else "disagree" if not any(moved) else "mixed"
    return value, [m["evidence_id"] for m in items]


def build_state(candidate: Any, bundle: Mapping[str, Any] | None,
                concurrent: Iterable[Any] = ()) -> tuple[JevState, dict[str, tuple[str, ...]]]:
    c = _d(candidate)
    if c["metric"] not in buckets.METRIC_FAMILY:
        raise StateError(f"no metric family for {c['metric']!r}")
    first = tuple(c["evidence_ids"][:1])
    scope = _dims(c["scope"])
    prov: dict[str, tuple[str, ...]] = {}
    values: dict[str, Any] = {
        "metric_family": buckets.METRIC_FAMILY[c["metric"]],
        "direction": c["direction"],
        "cadence": "intraday" if c["grain"] in buckets.INTRADAY_GRAINS else "daily",
        "scope_dims": tuple(sorted(k for k, _ in scope))[:MAX_DIMS] or ("global",),
        "relative_change": buckets.relative_change(c.get("relative_delta"), c["direction"]),
        "strength": buckets.strength(c.get("score_at_detection")),
        "detection_rule": buckets.detection_rule(tuple(c.get("methods_at_detection") or ())),
        "sample_size": buckets.sample_size(c.get("sample_size")),
    }
    for k in values:
        prov[k] = first
    if bundle is None:
        stage4 = dict.fromkeys(("change_type", "locus_kind", "locus_share", "cohorts_moved", "controls",
                                "related_metrics", "impact"), NOT_PROVIDED)
        stage4["locus_dims"] = ()
        values.update(stage4)
    else:
        bid = (bundle["bundle_id"],)
        label = bundle["label"]["value"]
        loc = bundle.get("locus") or {}
        locus = _dims(loc["cohort"]) if loc.get("cohort") is not None else None
        top = bundle.get("top_cohorts") or []
        top_ids = tuple(t["evidence_id"] for t in top[:1])
        kind = ("new_cohort" if label == "new_cohort" else "none" if locus is None
                else "whole_scope" if loc.get("is_scope") else "sub_cohort")
        share_value, share_ids = _locus_share(bundle, locus)
        related_value, related_ids = _related(bundle, c["direction"])
        controls = bundle.get("controls") or []
        imp = bundle.get("impact")
        values.update({
            "change_type": label,
            "locus_kind": kind,
            "locus_dims": tuple(sorted(k for k, v in (locus or []) if (k, v) not in scope))[:MAX_DIMS],
            "locus_share": share_value,
            "cohorts_moved": buckets.cohorts_moved(bundle.get("discoveries")),
            "controls": "present" if controls else "absent",
            "related_metrics": related_value,
            "impact": buckets.impact(imp["value"]) if imp else NOT_PROVIDED,
        })
        prov.update({"change_type": bid, "locus_kind": bid + top_ids, "locus_dims": bid + top_ids,
                     "locus_share": tuple(share_ids), "cohorts_moved": bid,
                     "controls": tuple(t["evidence_id"] for t in controls) or bid,
                     "related_metrics": tuple(related_ids), "impact": (top_ids or bid) if imp else ()})
    others = [_d(o) for o in concurrent]
    values["concurrent_alerts"] = buckets.concurrent(len(others))
    prov["concurrent_alerts"] = tuple(e for o in others for e in o["evidence_ids"][:1]) or first
    values["calendar_context"] = NOT_PROVIDED  # no typed calendar source exists (ADR-039 D-11)
    prov["calendar_context"] = ()
    state = JevState(**values)
    if len(canonical_json(state)) > MAX_STATE_BYTES:
        raise StateError(f"state of {len(canonical_json(state))} bytes exceeds {MAX_STATE_BYTES}")
    return state, {k: tuple(prov.get(k, ())) for k in STATE_FIELDS}


def state_dict(state: JevState) -> dict[str, Any]:
    return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(state).items()}


def canonical_json(obj: Any) -> bytes:
    """Sorted keys, compact separators, tuples as lists, explicit nulls, UTF-8 — one byte string per logical value."""
    if isinstance(obj, JevState):
        obj = state_dict(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def state_hash(state: JevState, schema_version: str = STATE_SCHEMA_VERSION) -> str:
    return hashlib.sha256(schema_version.encode() + b"\n" + canonical_json(state)).hexdigest()
