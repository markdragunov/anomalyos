"""Investigation agent, Mode B (architecture layer 8, Stage 7, ADR-049): a bounded, read-only, cited investigation.

The loop is code (ADR-019 option 3): code narrows the evidence, builds hypotheses from the closed cause vocabulary and a
shortlist of checks with predicted outcomes; a chooser (the deterministic control, or Jev — the fake while OQ-1 is
open) picks the next check; code runs it through a typed read-only tool and updates the hypotheses. Budgets are hard
stops (INV-011). Nothing here writes billing state, closes an incident or reads ground truth.
"""
