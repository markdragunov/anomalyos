# Baselines

Frozen traces and scores used to detect regressions.

Nothing is captured yet: there is no product runner. When the first real eval lands:

1. Run the scenario deterministically.
2. Write the trace under this directory (reviewed, not gitignored).
3. Fail CI on unexplained drift against that file.

Do not invent detector metrics to look complete.
