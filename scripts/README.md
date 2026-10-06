Harness entrypoints.

- `python scripts/check_architecture.py` — architecture constraint tests
- `python scripts/eval_smoke.py` — validate eval scenarios and write smoke traces
- `python scripts/check_typesafe_plugin.py` — compare the locally installed TypeSafe plugin with the version reviewed
  in ADR-047; run after installing or updating plugins (reads `~/.claude`, so not in CI)
