---
paths:
  - "src/pulseos/**/*.py"
  - "tests/**/*.py"
  - "docs/tasks/**/*.md"
---

# Stage guard

Purpose: build only the stage that was asked for.

- Before implementing, identify the active stage: *Stage status* in `docs/ARCHITECTURE.md`, `docs/specs/STATUS.md`,
  and the current brief in `docs/tasks/`. Say which stage the change belongs to.
- Implement only the requested scope. A later layer (cohorts, Jev, policy, incident engine, investigation, API, UI)
  needs its own request; the stage guard in `tests/architecture/test_no_product_implementation.py` lists the packages
  that do not exist yet.
- Respect the gates of the current brief: stop at every ⛔ and report; no code before a design gate is approved.
- A new dependency or top-level package needs explicit approval and an ADR before it is added (the dependency
  allowlist is pinned in the stage-guard test).
- If a request conflicts with the documented roadmap, an ADR or an invariant, stop and describe the conflict.
