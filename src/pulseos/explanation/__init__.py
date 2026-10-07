"""Explanation (architecture layer, Stage 7, ADR-027 option 3, ADR-049 D-7): an ``Explainer`` port, a slot-based
template explainer and the citation validator (INV-008, INV-013). No generative model: every number in a report comes
from an evidence slot, every claim cites evidence ids, and a report that fails validation is never returned."""
