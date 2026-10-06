Harness entrypoints.

- `python scripts/check_architecture.py` — architecture constraint tests
- `python scripts/eval_smoke.py` — validate eval scenarios and write smoke traces
- `python scripts/check_typesafe_plugin.py` — compare the locally installed TypeSafe plugin with the version reviewed
  in ADR-047; also runs as the SessionStart hook with `--hook` (ADR-048), silent unless something differs
  (reads `~/.claude`, so not in CI)
