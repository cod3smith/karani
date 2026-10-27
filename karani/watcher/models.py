"""Payloads that cross the watcher's boundaries (pydantic v2).

`InboundMessage` is the one that matters: everything on it is email the
world sent us, so every consumer treats it as DATA. Nothing in a message
— not the body, not the subject, not a header — is ever an instruction
to karani, and the classifier prompt says so explicitly.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# The classification taxonomy. `unclassified` is the degradation value:
# an LLM failure lands here rather than guessing or raising.
Label = Literal[
    "rejection",
    "interview_invite",
    "assessment",
    "scheduling",
    "offer",
    "recruiter_outreach",
    "irrelevant",
    "unclassified",
]

LABELS: tuple[str, ...] = (
    "rejection", "interview_invite", "assessment", "scheduling",
    "offer", "recruiter_outreach", "irrelevant", "unclassified",
)


class InboundMessage(BaseModel):
    """One message from the mailbox. Untrusted input, start to finish."""

    gmail_id: str
    thread_id: str = ""
    from_address: str = ""
    from_name: str = ""
    subject: str = ""
    body: str = ""
    received_at: datetime | None = None

    @property
    def from_domain(self) -> str:
        _, _, domain = self.from_address.partition("@")
        return domain.lower().strip(">").strip()

    def searchable(self, body_chars: int = 4000) -> str:
        """Sender name, subject and the head of the body — the text the
        matcher scans. Capped, because a quoted thread can be enormous
        and the useful signal is always near the top."""
        return "\n".join((self.from_name, self.subject,
                          self.body[:body_chars]))


class Classification(BaseModel):
    label: Label = "unclassified"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = ""
    # Provenance, same contract as QualificationResult.
    model: str = ""
    prompt_version: str = ""


class Match(BaseModel):
    """How (and how confidently) a message was tied to an application.

    A wrong match corrupts the funnel, so `confidence` is deliberately
    coarse: only `high` is allowed to move the state machine.
    """

    job_id: int | None = None
    matched_by: str = "none"
    confidence: Literal["high", "low", "none"] = "none"
    # Everything the matcher considered plausible — what gets reported
    # when it refuses to choose.
    candidates: tuple[int, ...] = ()
    note: str = ""


class ReplyDraft(BaseModel):
    """A reply for a human to read, edit and send. karani never sends."""

    subject: str = ""
    body: str = ""
    model: str = ""
    prompt_version: str = ""


class ProcessedMessage(BaseModel):
    message: InboundMessage
    classification: Classification
    match: Match
    status_before: str = ""
    status_after: str = ""
    reply: ReplyDraft | None = None


class WatchReport(BaseModel):
    """What one `karani watch` pass did — printed, and pushed to Slack."""

    fetched: int = 0
    already_seen: int = 0
    processed: list[ProcessedMessage] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def advanced(self) -> list[ProcessedMessage]:
        return [p for p in self.processed if p.status_after
                and p.status_after != p.status_before]

    @property
    def unmatched(self) -> list[ProcessedMessage]:
        """Reported, never guessed at (design 5.4)."""
        return [p for p in self.processed
                if p.match.confidence != "high"
                and p.classification.label not in ("irrelevant",)]
