"""Work-authorization answers, derived not generated.

Application forms ask legal questions — "are you authorized to work in
X", "will you require sponsorship" — and the answers are attestations
the candidate is accountable for. A drafting model improvising here
does not produce a typo; it produces a misrepresentation. So every
yes/no comes from a lookup against facts declared in karani.toml
`[legal]`, the same way the pre-filter stays deterministic (rule 4.9).
The LLM never answers these; it only receives the resolved line.

The answers are per-job because the same question has different correct
answers by role shape, which karani already classifies:

- **Globally-remote** (`global_hire_eligible: likely_yes`, fully
  remote): no visa exists in the transaction — the company engages him
  through an employer of record or as a contractor in his own country.
  Sponsorship is genuinely "no", and saying otherwise gets the
  application filtered out for a problem it does not have.
- **Relocation with sponsorship** (`relocation_support: likely_yes`):
  sponsorship is "yes", he is not authorized there yet, and he is
  willing to move.
- **Region-locked without sponsorship**: honestly unviable. Flagged
  rather than papered over.

When the geography cannot be determined the answer is "needs review" —
never an assumed yes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

# Conservative token -> country map. Only used to name the country a
# role sits in; an unmatched location yields review, never a guess.
_COUNTRY_TOKENS: tuple[tuple[str, str], ...] = (
    ("kenya", "Kenya"), ("nairobi", "Kenya"),
    ("united states", "United States"), ("usa", "United States"),
    ("u.s.", "United States"), ("(us", "United States"),
    ("us only", "United States"), ("us-only", "United States"),
    ("us remote", "United States"), ("us-remote", "United States"),
    (", us", "United States"),
    ("california", "United States"), ("new york", "United States"),
    ("san francisco", "United States"), ("seattle", "United States"),
    ("austin", "United States"), ("boston", "United States"),
    ("united kingdom", "United Kingdom"), ("london", "United Kingdom"),
    ("germany", "Germany"), ("berlin", "Germany"), ("munich", "Germany"),
    ("netherlands", "Netherlands"), ("amsterdam", "Netherlands"),
    ("ireland", "Ireland"), ("dublin", "Ireland"),
    ("france", "France"), ("paris", "France"),
    ("spain", "Spain"), ("portugal", "Portugal"), ("lisbon", "Portugal"),
    ("poland", "Poland"), ("estonia", "Estonia"), ("tallinn", "Estonia"),
    ("japan", "Japan"), ("tokyo", "Japan"),
    ("canada", "Canada"), ("toronto", "Canada"),
    ("australia", "Australia"), ("singapore", "Singapore"),
)

# Which relocation bloc a country belongs to, for matching against
# `relocation_open_to`.
_BLOCS: dict[str, str] = {
    "Germany": "EU", "Netherlands": "EU", "Ireland": "EU", "France": "EU",
    "Spain": "EU", "Portugal": "EU", "Poland": "EU", "Estonia": "EU",
    "Japan": "Japan",
}


def country_of(location_raw: str) -> str | None:
    text = (location_raw or "").lower()
    for token, country in _COUNTRY_TOKENS:
        if token in text:
            return country
    return None


@dataclass(frozen=True)
class WorkAuthAnswers:
    role_country: str | None
    # None when the role requires presence in no particular country.
    authorized_in_role_country: bool | None
    requires_sponsorship: bool
    willing_to_relocate: bool
    viable: bool
    needs_review: bool
    note: str
    earliest_start: str
    engagement: tuple[str, ...]

    def form_answers(self) -> dict[str, str]:
        """The canonical questions ATS forms ask, as they'd be typed."""
        if self.authorized_in_role_country is None:
            authorized = "Not applicable — fully remote, no work location"
        else:
            authorized = "Yes" if self.authorized_in_role_country else "No"
        return {
            "work_authorization": authorized,
            "requires_sponsorship": "Yes" if self.requires_sponsorship else "No",
            "willing_to_relocate": "Yes" if self.willing_to_relocate else "No",
            "earliest_start": self.earliest_start,
            "work_location": self.note,
        }


def _start(notice_days: int) -> str:
    if notice_days <= 0:
        return "Immediately"
    return f"{notice_days} days from offer"


def _engagement_phrase(engagement: tuple[str, ...]) -> str:
    names = {"employer_of_record": "an employer of record (Deel, Remote, "
                                   "Oyster or similar)",
             "contractor": "a direct contractor agreement",
             "employee": "direct employment"}
    parts = [names[e] for e in engagement if e in names]
    if not parts:
        return "a locally compliant arrangement"
    if len(parts) == 1:
        return parts[0]
    return " or ".join((", ".join(parts[:-1]), parts[-1]))


def _as_dict(value) -> dict:
    """JSONB columns arrive as str from asyncpg (no codec is
    registered), as dict from the in-memory store. Both are normal."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def resolve_work_authorization(job_row: dict, legal) -> WorkAuthAnswers:
    """Per-job legal answers from declared facts. Never calls a model."""
    pf = _as_dict(job_row.get("prefilter"))
    remote = pf.get("fully_remote")
    global_hire = pf.get("global_hire_eligible")
    sponsors = pf.get("relocation_support") == "likely_yes"

    country = country_of(job_row.get("location_raw") or "")
    authorized_countries = tuple(legal.work_authorized_in or ())
    start = _start(int(legal.notice_period_days or 0))
    based = legal.based_in_country or "my country of residence"

    # Shape one: hired globally, works from home country. No visa exists.
    if remote == "yes" and global_hire == "likely_yes":
        return WorkAuthAnswers(
            role_country=None, authorized_in_role_country=None,
            requires_sponsorship=False, willing_to_relocate=bool(
                legal.relocation_open_to),
            viable=True, needs_review=False,
            note=(f"I work remotely from {based}, where I am authorized to "
                  f"work. No visa or sponsorship is required: companies "
                  f"engage me through {_engagement_phrase(tuple(legal.remote_engagement or ()))}."),
            earliest_start=start,
            engagement=tuple(legal.remote_engagement or ()),
        )

    # Role sits in a country he is already authorized in.
    if country and country in authorized_countries:
        return WorkAuthAnswers(
            role_country=country, authorized_in_role_country=True,
            requires_sponsorship=False, willing_to_relocate=False,
            viable=True, needs_review=False,
            note=f"I am a citizen of {country} and authorized to work there.",
            earliest_start=start,
            engagement=tuple(legal.remote_engagement or ()),
        )

    # Geography unknown. If the pre-filter already judged the role
    # region-locked, that verdict stands even though we cannot name the
    # country: answering "no sponsorship needed" here would be a false
    # attestation, not a near miss.
    if country is None and global_hire == "likely_no":
        return WorkAuthAnswers(
            role_country=None, authorized_in_role_country=False,
            requires_sponsorship=True,
            willing_to_relocate=bool(legal.relocation_open_to),
            viable=bool(sponsors), needs_review=True,
            note=("This role is tied to a region I am not authorized to "
                  "work in and would require sponsorship. The posting does "
                  "not name the country — confirm before answering."),
            earliest_start=start,
            engagement=tuple(legal.remote_engagement or ()),
        )

    # Geography unknown and unrestricted — say so rather than assume.
    if country is None:
        return WorkAuthAnswers(
            role_country=None, authorized_in_role_country=None,
            requires_sponsorship=sponsors, willing_to_relocate=bool(
                legal.relocation_open_to),
            viable=True, needs_review=True,
            note=("Work location unclear from the posting — confirm whether "
                  "this is globally remote or tied to a country before "
                  "answering authorization questions."),
            earliest_start=start,
            engagement=tuple(legal.remote_engagement or ()),
        )

    # Somewhere he is not authorized: sponsorship required. Viable only
    # if they sponsor and he'd move there.
    bloc = _BLOCS.get(country, country)
    open_to = tuple(legal.relocation_open_to or ())
    would_move = bloc in open_to or country in open_to
    note = (f"I am not currently authorized to work in {country} and would "
            f"require visa sponsorship. ")
    note += ("I am willing to relocate and have prioritized this region."
             if would_move else
             "I would consider relocation for the right role.")
    return WorkAuthAnswers(
        role_country=country, authorized_in_role_country=False,
        requires_sponsorship=True, willing_to_relocate=would_move,
        viable=bool(sponsors and would_move), needs_review=False,
        note=note, earliest_start=start,
        engagement=tuple(legal.remote_engagement or ()),
    )
