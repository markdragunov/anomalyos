"""Evidence objects with stable ids and per-field epistemic labels (INV-009, INV-013), and the per-investigation
registry the explainer and the citation validator read from."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

LABELS = ("observed", "inferred", "estimated", "recommended")


def evidence_id(tool: str, args: Any, run_id: str, as_of: int, version: str) -> str:
    blob = json.dumps([tool, args, run_id, as_of, version], sort_keys=True, default=str)
    return "evd_" + hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    tool: str
    args: tuple
    payload: dict[str, Any]
    labels: dict[str, str]  # field path -> epistemic label
    as_of: int


def field_value(payload: dict[str, Any], path: str) -> Any:
    """``a.b.0.c`` style path into a payload; raises KeyError if absent."""
    cur: Any = payload
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


@dataclass
class Registry:
    items: dict[str, Evidence] = field(default_factory=dict)

    def add(self, e: Evidence) -> Evidence:
        self.items.setdefault(e.evidence_id, e)
        return self.items[e.evidence_id]

    def get(self, evidence_id: str) -> Evidence | None:
        return self.items.get(evidence_id)

    def __len__(self) -> int:
        return len(self.items)
