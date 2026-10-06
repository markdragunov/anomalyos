"""Claims, reports, the ``Explainer`` port and the template explainer (ADR-049 D-7).

A claim = template id + slots; a slot = (evidence id, field path, epistemic label). The text is rendered from the
template and the slot values read from the evidence registry — templates contain no literal numbers. Language follows
D-4: "consistent with", "evidence against", "insufficient evidence"; never a causal certainty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pulseos.investigation.evidence import Registry, field_value


@dataclass(frozen=True)
class Slot:
    evidence_id: str
    field: str
    label: str


@dataclass(frozen=True)
class Claim:
    template: str
    slots: tuple[Slot, ...]
    text: str = ""


@dataclass
class Report:
    investigation_id: str
    claims: list[Claim] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


class Explainer(Protocol):
    def explain(self, investigation, registry: Registry) -> Report: ...


TEMPLATES = {
    "summary": "Incident {0} started at {1}; first detected at {2}.",
    "locus": "The change is located in {0} (Stage 4 analysis).",
    "consistent": "Evidence consistent with {cause}: {0} observed {1}, as that cause predicts.",
    "against": "Evidence against {cause}: {0} — observed {1}.",
    "side_support": "Context consistent with {cause}: {0}.",
    "insufficient": "Insufficient evidence for {cause}.",
    "impact": "Estimated lost successful payments so far: {0}.",
    "next": "Recommended next check for a human: {0}.",
}


def _render(template: str, slots: tuple[Slot, ...], registry: Registry, **kw) -> str:
    vals = []
    for s in slots:
        e = registry.get(s.evidence_id)
        vals.append(field_value(e.payload, s.field) if e is not None else "?")
    return TEMPLATES[template].format(*vals, **kw)


class TemplateExplainer:
    """Renders claims only from typed investigation state and evidence; the validator runs afterwards."""

    def explain(self, inv, registry: Registry) -> Report:
        rep = Report(inv.investigation_id)
        inc = next((e for e in registry.items.values() if e.tool == "get_incident"), None)

        def add(template: str, slots: tuple[Slot, ...], **kw) -> None:
            try:
                text = _render(template, slots, registry, **kw)
            except (KeyError, IndexError, ValueError, TypeError):
                text = ""
            rep.claims.append(Claim(template, slots, text))

        if inc is not None:
            add("summary", (Slot(inc.evidence_id, "incident_id", "observed"), Slot(inc.evidence_id, "started_at", "observed"),
                            Slot(inc.evidence_id, "detected_at", "observed")))
            add("locus", (Slot(inc.evidence_id, "locus", "inferred"),))
        for h in inv.hypotheses:
            for eid in h.supporting[:3]:
                e = registry.get(eid)
                if e is not None and "outcome" in e.payload:
                    add("consistent", (Slot(eid, "metric", "observed"), Slot(eid, "outcome", "inferred")), cause=h.cause)
                elif e is not None and "present" in e.payload:
                    add("side_support", (Slot(eid, "present", "observed"),), cause=h.cause)
                elif e is not None and "components" in e.payload:
                    add("side_support", (Slot(eid, "components", "observed"),), cause=h.cause)
            for eid in h.contradicting[:3]:  # contradicting evidence is always reported
                add("against", (Slot(eid, "metric", "observed"), Slot(eid, "outcome", "inferred")), cause=h.cause)
            if h.status == "insufficient_evidence" and inc is not None:
                add("insufficient", (Slot(inc.evidence_id, "incident_id", "observed"),), cause=h.cause)
        imp = registry.get(inv.impact_evidence) if inv.impact_evidence else None
        if imp is not None and imp.payload.get("lost_successful_payments") is not None:
            add("impact", (Slot(inv.impact_evidence, "lost_successful_payments", "estimated"),))
        return rep
