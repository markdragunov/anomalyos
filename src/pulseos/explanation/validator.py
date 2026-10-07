"""Citation validator (INV-008, ADR-049 D-7): runs before any report is returned.

Every claim has ≥ 1 slot; every slot's evidence id is in the investigation's registry; its field exists; its label is
from the epistemic enum and equals the label the evidence gives that field (an ``estimated`` value can only come from
an ``estimated`` field); the text rendered. A report with any error is not shown (``investigation_status = failed``).
"""

from __future__ import annotations

from pulseos.explanation.report import Report
from pulseos.investigation.evidence import LABELS, Registry, field_value


def validate(report: Report, registry: Registry) -> Report:
    errors = []
    for i, c in enumerate(report.claims):
        if not c.slots:
            errors.append(f"claim {i} ({c.template}): no evidence cited")
        for s in c.slots:
            e = registry.get(s.evidence_id)
            if e is None:
                errors.append(f"claim {i}: unknown evidence id {s.evidence_id}")
                continue
            try:
                field_value(e.payload, s.field)
            except (KeyError, IndexError, ValueError):
                errors.append(f"claim {i}: field {s.field} not in {s.evidence_id}")
                continue
            if s.label not in LABELS:
                errors.append(f"claim {i}: label {s.label!r} outside the enum")
            elif e.labels.get(s.field.split(".")[0]) not in (None, s.label):
                errors.append(f"claim {i}: label {s.label} differs from the evidence's {e.labels[s.field.split('.')[0]]}")
            elif s.label == "estimated" and e.labels.get(s.field.split(".")[0]) != "estimated":
                errors.append(f"claim {i}: estimated value from a field not labelled estimated")
        if not c.text:
            errors.append(f"claim {i} ({c.template}): did not render")
    report.errors = errors
    return report
