"""The last mile: the form this job asks, the answers, and the console.

karani never submits (ADR 0012). This package prepares a submission so
thoroughly that sending it is a scan and a click, and then records that
it happened — which is what moves `applied` off zero.
"""
from __future__ import annotations

from .questions import (
    Choice, Question, QuestionSet, introspect_questions,
    questions_from_payload, standard_questions,
)

__all__ = ["Choice", "Question", "QuestionSet", "introspect_questions",
           "questions_from_payload", "standard_questions"]
