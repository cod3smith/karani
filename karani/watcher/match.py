"""Tie a message to an application — deterministically, and timidly.

A wrong match is worse than no match. It stamps a rejection on the wrong
company, moves a live application to a terminal state, and poisons the
only outcome labels the learning loop will ever have (design 4). So this
module refuses whenever the evidence is thin, and the runner reports the
refusals instead of guessing.

No LLM here, deliberately — same tier split as the pre-filter
(CLAUDE.md 4.9). The signals, strongest first:

1. **Thread.** Another message in this thread already matched. Replies
   quote nothing useful, so this is the cheapest strong signal there is.
2. **Company name**, word-boundary anchored (CLAUDE.md 4.2 — "us only"
   matching inside "campus only" is the bug this project already paid
   for once), looked for in the sender's display name, the subject and
   the head of the body.
3. **Sender domain**: a label of the sending domain equal to the company
   handle, which covers both `people@globex.com` and the ATS pattern
   `no-reply@globex.greenhouse.io`.

An ATS sender domain on its own never matches anything — it says which
system sent the mail, not which company. It only raises confidence in a
company match that was already found.

Nothing in the message body gets a vote on its own job id. Email is
data (see `classify`), and "job_id: 11" in a body is a claim, not a key.
"""
from __future__ import annotations

import re

from .models import InboundMessage, Match

# Applications that can plausibly receive a reply. A posting karani has
# only ingested cannot be rejected.
IN_FLIGHT: frozenset[str] = frozenset({
    "ready", "applied", "screen", "interview", "offer",
})

# Registrable domains of the applicant-tracking systems karani ingests
# from, plus the big ones it will meet in the inbox anyway.
ATS_DOMAINS: frozenset[str] = frozenset({
    "greenhouse.io", "greenhouse-mail.io", "us.greenhouse-mail.io",
    "lever.co", "hire.lever.co", "lever.email",
    "ashbyhq.com", "ashbyhq.email",
    "myworkday.com", "workday.com", "myworkdayjobs.com",
    "workable.com", "workablemail.com",
    "smartrecruiters.com", "icims.com", "jobvite.com", "bamboohr.com",
    "successfactors.com", "taleo.net", "breezy.hr", "teamtailor-mail.com",
})

# Corporate suffixes that are noise in a name match.
_SUFFIXES = re.compile(
    r"(?i)[,\s]+(inc|inc\.|llc|ltd|ltd\.|limited|corp|corp\.|corporation|"
    r"gmbh|bv|b\.v\.|ab|oy|sa|s\.a\.|plc|co|co\.|holdings|group)$")

# Company names that are also ordinary words. Matching one of these on
# the name alone would tie every newsletter to the application.
_GENERIC = frozenset({
    "data", "cloud", "remote", "open", "fast", "front", "stack", "base",
    "better", "even", "now", "one", "two", "box", "slack", "notion",
    "mercury", "ramp", "scale", "block", "chime", "nord", "canvas",
    "the", "and", "for", "with", "also", "mail", "jobs", "job", "work",
})

_MIN_NAME = 3


def _registrable(domain: str) -> str:
    parts = [p for p in (domain or "").lower().split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else ""


def is_ats_domain(domain: str) -> bool:
    """True when the message came through a known ATS.

    Label-anchored on purpose: `notgreenhouse.io` is not Greenhouse, and
    neither is `greenhouse.io.evil.com`.
    """
    domain = (domain or "").lower().strip(".")
    if not domain:
        return False
    if domain in ATS_DOMAINS or _registrable(domain) in ATS_DOMAINS:
        return True
    return any(domain.endswith("." + known) for known in ATS_DOMAINS)


def _handle(row: dict) -> str:
    raw = row.get("company") or row.get("company_display") or ""
    return re.sub(r"[^a-z0-9]+", "", raw.lower())


def _name_forms(row: dict) -> list[str]:
    """Name spellings worth looking for, longest first."""
    display = (row.get("company_display") or row.get("company") or "").strip()
    forms = {display}
    stripped = _SUFFIXES.sub("", display).strip()
    if stripped:
        forms.add(stripped)
    forms = {f for f in forms
             if len(re.sub(r"[^a-z0-9]+", "", f.lower())) >= _MIN_NAME
             and f.lower() not in _GENERIC}
    return sorted(forms, key=len, reverse=True)


def _name_pattern(form: str) -> re.Pattern[str]:
    # Whitespace in a name may arrive collapsed or wrapped.
    body = r"\s+".join(re.escape(tok) for tok in form.split())
    return re.compile(rf"(?i)(?<![\w-]){body}(?![\w-])")


def _domain_names(message: InboundMessage) -> set[str]:
    return {label for label in message.from_domain.split(".") if label}


def _title_tokens(title: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z][A-Za-z+#.-]{2,}", title or "")
            if t.lower() not in {"the", "and", "for", "senior", "staff",
                                 "engineer", "principal", "lead", "new"}]


def match_message(
    message: InboundMessage,
    applications: list[dict],
    *,
    thread_index: dict[str, int] | None = None,
) -> Match:
    """Which application is this message about? `None` is a fine answer."""
    thread_index = thread_index or {}

    if message.thread_id and message.thread_id in thread_index:
        return Match(job_id=thread_index[message.thread_id],
                     matched_by="thread", confidence="high")

    pool = [row for row in applications
            if (row.get("application_status") or "") in IN_FLIGHT]
    if not pool:
        return Match(note="no applications are in flight")

    haystack = message.searchable()
    domain_labels = _domain_names(message)
    ats = is_ats_domain(message.from_domain)

    by_company: dict[str, list[dict]] = {}
    signals: dict[str, set[str]] = {}
    for row in pool:
        handle = _handle(row)
        hit = ""
        if handle and handle in domain_labels:
            hit = "sender_domain"
        elif any(_name_pattern(form).search(haystack)
                 for form in _name_forms(row)):
            hit = "company_name"
        if not hit:
            continue
        by_company.setdefault(handle, []).append(row)
        signals.setdefault(handle, set()).add(hit)

    if not by_company:
        return Match(note=f"no tracked company named in mail from "
                          f"{message.from_domain or 'an unknown sender'}")

    if len(by_company) > 1:
        # Two tracked companies in one message is a newsletter, not a
        # reply. Report it; never pick.
        candidates = tuple(sorted(row["id"] for rows in by_company.values()
                                  for row in rows))
        return Match(candidates=candidates, confidence="none",
                     note="several tracked companies named in one message")

    handle, rows = next(iter(by_company.items()))
    how = "+".join(sorted(signals[handle] | ({"ats"} if ats else set())))

    if len(rows) == 1:
        return Match(job_id=rows[0]["id"], matched_by=how, confidence="high")

    # Same company, several open applications: only a title separates
    # them, and if it does not, we say so.
    scored = [
        (sum(1 for token in _title_tokens(row.get("title", ""))
             if _name_pattern(token).search(haystack)), row)
        for row in rows
    ]
    best = max(score for score, _ in scored)
    leaders = [row for score, row in scored if score == best]
    if best > 0 and len(leaders) == 1:
        return Match(job_id=leaders[0]["id"], matched_by=f"{how}+title",
                     confidence="high")
    return Match(candidates=tuple(sorted(row["id"] for row in rows)),
                 confidence="low", matched_by=how,
                 note=f"{len(rows)} open applications at the same company")
