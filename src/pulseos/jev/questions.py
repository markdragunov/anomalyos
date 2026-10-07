"""Question set v1 (ADR-039 D-3; resolves OQ-5). One judgment per question, no numbers or dates in the wording.

The cause vocabulary is ``docs/DATA_MODEL.md`` v1 (the same labels as ground-truth causes; a unit test checks they
match the simulator's list). Severity ranges over ``low … critical``; ``none`` is carried by ``is_incident``.
"""

from __future__ import annotations

from dataclasses import dataclass

QUESTION_SET_VERSION = "question_set_v1"
CAUSES_V1 = (
    "psp_degradation", "payment_method_degradation", "issuer_or_country_degradation", "checkout_regression",
    "renewal_job_failure", "dunning_failure", "refund_process_change", "duplicate_charging", "fraud_attack",
    "pricing_or_plan_change", "data_pipeline_issue", "normal_variation", "unknown",
)
SEVERITIES = ("low", "medium", "high", "critical")


@dataclass(frozen=True)
class Question:
    id: str
    kind: str  # "noul" (probability) | "choice" (probability per option + confidence)
    wording: str
    options: tuple[str, ...] = ()


QUESTIONS_V1 = (
    Question("is_incident", "noul",
             "Is this change a real operational problem rather than normal variation in traffic or behaviour?"),
    Question("severity", "choice", "If this is a real problem, how severe is it for the business?", SEVERITIES),
    Question("category", "choice", "Which cause best fits this change?", CAUSES_V1),
    Question("needs_human", "noul",
             "Is the evidence ambiguous enough that a person should review it before anyone is paged?"),
)
QUESTION_IDS = tuple(q.id for q in QUESTIONS_V1)


# Stage 7 (ADR-049 D-8): the investigation question set. Options and question ids are built per request from the
# typed investigation state (chunk and step ids); the wording references the state paths it judges.
INVESTIGATION_QUESTION_SET_VERSION = "investigation_question_set_v1"


def chunk_question(i: int) -> Question:
    return Question(f"chunk_{i}", "noul", f"Does the group of cohorts `chunks[{i}]` hold most of the change?")


def step_question(options: tuple[str, ...]) -> Question:
    return Question("next_step", "choice", "Which next check in `shortlist` best separates the leading explanations, "
                    "or should the investigation stop?", tuple(options) + ("stop",))


def hypothesis_question(cause: str) -> Question:
    return Question(f"supports_{cause}", "noul", f"Is the evidence in `evidence` consistent with the cause `{cause}`?")
