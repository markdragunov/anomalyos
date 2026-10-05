"""Run orchestration: generate a world, stream it to sinks, fingerprint it.

Why: acceptance requires that a seed reproduces an identical dataset. We prove that with a
SHA-256 over the canonical event stream (one JSON line per event, in emission order) and a
separate digest over ground truth, both recorded in a manifest.

Input:  ``WorldConfig``, preset name, optional output directory, optional extra sinks.
Output: ``RunResult`` (run_id, digests, per-type counts, ground truth). With ``out_dir``:
        ``events.jsonl.gz`` · ``ground_truth.json`` · ``manifest.json`` (ground truth is in its own
        file on purpose — never co-located inside the event stream).
Invariants
* ``run_id`` is a pure function of (seed, preset, world config, scenario specs, generator version).
* gzip header mtime is fixed to 0 so the compressed file is byte-reproducible too.
* No wall-clock values in any output.
Failure modes: filesystem errors propagate (``OSError``); an existing non-empty ``out_dir`` is
refused unless ``overwrite=True``.
"""

from __future__ import annotations

import dataclasses
import gzip
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .engine import Simulation
from .ground_truth import GENERATOR_VERSION, GroundTruth, truth_digest
from .ids import short_hash
from .models import API_VERSION, Event
from .scenarios import build_catalog
from .world import WorldConfig, iso

EventSink = Callable[[Iterable[Event]], None]


@dataclass(frozen=True)
class RunResult:
    run_id: str
    seed: int
    preset: str
    events: int
    events_digest: str
    truth_digest: str
    counts_by_type: dict[str, int]
    ground_truth: list[GroundTruth]
    manifest: dict

    def summary(self) -> str:
        inc = sum(1 for g in self.ground_truth if g.incident_id)
        return (f"{self.run_id}: {self.events:,} events · {len(self.ground_truth)} truth records "
                f"({inc} incidents) · events sha256 {self.events_digest[:16]}… · truth {self.truth_digest[:16]}…")


# WorldConfig fields added after sim-1.0.0. They enter the run_id hash only when they differ from their default,
# so a run that does not use them keeps the id (and digests) it had before the field existed (ADR-029).
_POST_1_0_FIELDS = ("schedule", "realism", "hourly_noise_sd", "weekend_approval_factor")


def _hashed_config(world: WorldConfig) -> dict:
    cfg = dataclasses.asdict(world)
    defaults = {f.name: f.default for f in dataclasses.fields(WorldConfig)}
    for name in _POST_1_0_FIELDS:
        if cfg.get(name) == defaults[name]:
            del cfg[name]
    return cfg


def run_id_for(world: WorldConfig, preset: str, specs=None) -> str:
    """Pure function of generator version, preset, world config and every scenario spec hash."""
    specs = build_catalog(world, preset) if specs is None else specs
    cfg = json.dumps(_hashed_config(world), sort_keys=True)
    return "run_" + short_hash(GENERATOR_VERSION, preset, cfg, *(s.spec_hash for s in specs))[:16]


def generate(world: WorldConfig, preset: str = "full", out_dir: str | Path | None = None, *,
             overwrite: bool = False, on_event: Callable[[Event, str], None] | None = None) -> RunResult:
    specs = build_catalog(world, preset)
    sim = Simulation(world, specs)
    rid = run_id_for(world, preset, specs)
    digest = hashlib.sha256()
    n = 0

    out = Path(out_dir) if out_dir is not None else None
    gz = None
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        if any(out.iterdir()) and not overwrite:
            raise FileExistsError(f"{out} is not empty (use overwrite=True / --overwrite)")
        raw = open(out / "events.jsonl.gz", "wb")
        gz = gzip.GzipFile(filename="events.jsonl", mode="wb", fileobj=raw, compresslevel=3, mtime=0)
    try:
        for ev in sim.run():
            line = ev.to_json_line()
            b = (line + "\n").encode("utf-8")
            digest.update(b)
            if gz is not None:
                gz.write(b)
            if on_event is not None:
                on_event(ev, line)
            n += 1
    finally:
        if gz is not None:
            gz.close()
            raw.close()

    truth = sim.ground_truth()
    counts = dict(sorted(Counter(sim.stats).items()))
    manifest = {
        "run_id": rid,
        "generator_version": GENERATOR_VERSION,
        "api_version": API_VERSION,
        "seed": world.seed,
        "preset": preset,
        "world": dataclasses.asdict(world),
        "window": {"start": world.start, "end": world.end, "start_iso": iso(world.start), "end_iso": iso(world.end)},
        "scenarios": [{"scenario_id": s.scenario_id, "kind": s.kind, "title": s.title, "seed": s.seed,
                       "start": s.start, "end": s.end, "severity": s.severity.value, "spec_hash": s.spec_hash}
                      for s in specs],
        "events": n,
        "events_sha256": digest.hexdigest(),
        "truth_digest": truth_digest(truth),
        "counts_by_type": counts,
    }
    if out is not None:
        (out / "ground_truth.json").write_text(
            json.dumps({"run_id": rid, "records": [g.to_dict() for g in truth]}, indent=2, sort_keys=True) + "\n")
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return RunResult(rid, world.seed, preset, n, digest.hexdigest(), manifest["truth_digest"], counts, truth, manifest)


def read_events(path: str | Path) -> Iterable[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)
