"""The last mile: the form this job asks, the answers, and the console.

karani never submits (ADR 0012). This package prepares a submission so
thoroughly that sending it is a scan and a click, and then records that
it happened — which is what moves `applied` off zero.
"""
from __future__ import annotations

from .answers import Answer, AnswerSheet, build_answer_sheet
from .console import render_console
from .pack import PackContent, load_pack, parse_pack_markdown
from .questions import (
    Choice, Question, QuestionSet, introspect_questions,
    introspection_enabled, questions_from_payload, standard_questions,
)
from .runner import ConsoleResult, build_console

__all__ = ["Answer", "AnswerSheet", "Choice", "ConsoleResult", "PackContent",
           "Question", "QuestionSet", "build_answer_sheet", "build_console",
           "introspect_questions", "introspection_enabled", "load_pack",
           "parse_pack_markdown", "questions_from_payload", "render_console",
           "standard_questions"]
