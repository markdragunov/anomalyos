"""Replay artifacts (ADR-039 D-5): every answered request is stored once and can be replayed byte for byte.

Artifacts are JSON lines keyed by request hash, kept under ``data/jev_replay/`` (gitignored) or a test directory;
they are replay / evaluation artifacts, not the runtime audit (that is ``policy.audit``). Secrets are redacted before
writing. A replay miss is an error, never a silent fake.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from anomalyos.jev.client import DecisionContext, JevClient, JevError, JevRequest, JevResponse

ARTIFACT_VERSION = 1
_SECRET_KEY = re.compile(r"key|token|secret|authorization|password", re.I)


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("[redacted]" if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


class ReplayStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.records: dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.records[r["request_hash"]] = r

    def append(self, request: JevRequest, outcome: JevResponse | JevError, ctx: DecisionContext) -> None:
        rec = {"artifact_version": ARTIFACT_VERSION, "request_hash": request.request_hash,
               "state_hash": request.state_hash, "state_schema_version": request.state_schema_version,
               "question_set_version": request.question_set_version, "requested_model": request.requested_model,
               "evaluated_at": ctx.evaluated_at}
        if isinstance(outcome, JevError):
            rec["error"] = asdict(outcome)
        else:
            rec["response"] = redact(asdict(outcome))
        rec = redact(rec)
        self.records[request.request_hash] = rec
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")


class RecordingClient:
    """Asks the inner client and stores the outcome. With a live transport this needs owner approval (D-5)."""

    def __init__(self, inner: JevClient, store: ReplayStore) -> None:
        self.inner, self.store, self.name = inner, store, inner.name

    def ask(self, request: JevRequest, ctx: DecisionContext) -> JevResponse | JevError:
        outcome = self.inner.ask(request, ctx)
        self.store.append(request, outcome, ctx)
        return outcome


class ReplayJevClient:
    name = "replay"

    def __init__(self, store: ReplayStore) -> None:
        self.store = store

    def ask(self, request: JevRequest, ctx: DecisionContext) -> JevResponse | JevError:
        rec = self.store.records.get(request.request_hash)
        if rec is None:
            return JevError("replay_miss", "no recorded response", request.request_hash, self.name)
        if "error" in rec:
            return JevError(**rec["error"])
        r = dict(rec["response"])
        r["answers"] = tuple((qid, payload) for qid, payload in r["answers"])
        return JevResponse(**r)
