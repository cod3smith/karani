"""What a label does to the application state machine.

One rule above the others: **the ladder only climbs.** Mail arrives out
of order — a calendar reschedule lands after the onsite, a recruiter
re-sends an old invite — and a watcher that let any of that walk an
application backwards would corrupt the funnel just as thoroughly as a
wrong match. So a transition is applied only when it is strictly higher
than where the application already is, except for the two terminal
answers, which are always the truth.

Statuses here are the ones `Storage.APPLICATION_STATUSES` already
accepts (ADR 0005); nothing new is invented. A label that says nothing
about state returns None and the runner records a stage note instead —
the evidence survives either way, in the `stages` JSONB log.
"""
from __future__ import annotations

# Ascending. Only `>` moves the application.
RANK: dict[str, int] = {
    "new": 0, "drafting": 1, "ready": 2, "applied": 3,
    "screen": 4, "interview": 5, "offer": 6,
}

TERMINAL: frozenset[str] = frozenset({"rejected", "declined", "ghosted"})

# The label -> status the message argues for. Absent labels (scheduling,
# recruiter_outreach, irrelevant, unclassified) argue for nothing.
_CLAIMS: dict[str, str] = {
    "rejection": "rejected",
    "offer": "offer",
    "assessment": "screen",
}


def next_status(label: str, current: str | None) -> str | None:
    """The status to move to, or None to leave the application alone."""
    current = current or "new"
    if current in TERMINAL:
        # Someone writing to a closed application is a human problem.
        return None

    if label in ("rejection", "offer"):
        claimed = _CLAIMS[label]
        return None if claimed == current else claimed

    if label == "interview_invite":
        # First invite opens a screen; a second one means they are
        # taking us past it.
        claimed = "interview" if RANK.get(current, 0) >= RANK["screen"] \
            else "screen"
    elif label in _CLAIMS:
        claimed = _CLAIMS[label]
    else:
        return None

    if RANK.get(claimed, 0) > RANK.get(current, 0):
        return claimed
    return None
