"""The response watcher — karani finally learns what happened.

karani drafted 121 packs and knew the outcome of none of them: replies
land in Gmail, nothing read them, so the state machine never moved past
`drafting` and there was no outcome label for any learning loop
(design 5.4).

The pass: read recent mail over a READ-ONLY Gmail scope, classify each
message, match it to an application conservatively, advance the state
machine only on a confident match, and draft a reply for review. It
never sends, never deletes, never marks anything read — the scope makes
all three impossible, which is why the scope was chosen.

Modules: `gmail` (transport), `classify` (LLM, degrades to
`unclassified`), `match` (deterministic), `state` (the ladder),
`reply` (drafts), `store` (dedup ledger), `runner` (the pass),
`blocks` (Slack delivery). See docs/watcher.md.
"""
from __future__ import annotations

from .gmail import GMAIL_READONLY_SCOPE, SCOPES, GmailClient, GmailAuthError
from .models import (
    Classification,
    InboundMessage,
    Match,
    ProcessedMessage,
    ReplyDraft,
    WatchReport,
)

from .runner import watch

__all__ = [
    "GMAIL_READONLY_SCOPE", "SCOPES", "GmailClient", "GmailAuthError",
    "Classification", "InboundMessage", "Match", "ProcessedMessage",
    "ReplyDraft", "WatchReport", "watch",
]
