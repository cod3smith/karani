"""One answer per question, and where each answer came from.

The form asks twelve things; karani already knows eleven of them. The
work is matching a question to the fact that answers it, and being
explicit about provenance, because the sources are not equally
trustworthy and must not be interchangeable:

- **legal** — `karani/legal.py`, resolved per job from declared facts.
  Authorization and sponsorship answers are attestations the candidate
  signs, so they are derived, never model-written. A drafted pack that
  happens to contain a sponsorship answer is ignored for those fields.
- **profile** — the merged profile core (name, contacts, location).
- **document** — a presigned link to the rendered resume / letter.
- **pack** — prose the drafter already wrote for this job.
- **job** — facts off the posting (the published comp band).
- **voluntary** — demographic self-identification, deliberately blank.
- nothing — flagged `needs_input` rather than invented.

Everything here is deterministic. No LLM call, no network.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from karani.legal import resolve_work_authorization


class Answer(BaseModel, frozen=True):
    key: str
    label: str
    kind: str = "text"
    value: str = ""
    source: str = ""          # legal|profile|document|pack|job|voluntary|""
    required: bool = False
    needs_input: bool = False
    note: str = ""


class AnswerSheet(BaseModel, frozen=True):
    job_id: int = 0
    company: str = ""
    title: str = ""
    apply_url: str = ""
    ats: str = ""
    introspected: bool = False
    question_note: str = ""
    legal_note: str = ""
    viable: bool = True
    needs_review: bool = False
    answers: tuple[Answer, ...] = ()
    documents: dict[str, str] = {}

    @property
    def outstanding(self) -> int:
        return sum(1 for a in self.answers if a.needs_input)

    @property
    def answered(self) -> int:
        return sum(1 for a in self.answers
                   if a.value and a.source != "voluntary")


# --- question -> slot ------------------------------------------------
# Stems are deliberate ("authoriz" catches authorized/authorisation).
# The lookbehind is the word-boundary discipline of rule 4.2: without
# it "resume" matches "presumed" and "cv" matches "cvs" in prose.

_SLOTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # First, because "voluntary self-identification: disability" also
    # contains words that look like other slots.
    ("voluntary", (r"gender", r"\brace\b", r"ethnic", r"veteran",
                   r"disabilit", r"pronoun", r"sexual orientation",
                   r"self-identification", r"date of birth",
                   r"transgender")),
    ("sponsorship", (r"sponsor", r"\bvisa\b", r"work permit")),
    ("work_auth", (r"authoriz", r"authoris", r"right to work",
                   r"eligible to work", r"legally (?:able|entitled|allowed)",
                   r"work eligibility")),
    ("relocate", (r"relocat", r"willing to move")),
    ("start", (r"start date", r"earliest", r"notice period",
               r"when (?:can|could|would) you start", r"availabilit")),
    ("salary", (r"salary", r"compensation", r"expected pay",
                r"pay expectation", r"desired (?:pay|rate)",
                r"hourly rate", r"day rate")),
    ("resume", (r"resume", r"\bcv\b", r"curriculum vitae")),
    ("cover_letter", (r"cover letter",)),
    ("first_name", (r"first name", r"given name")),
    ("last_name", (r"last name", r"family name", r"surname")),
    ("email", (r"e-?mail",)),
    ("phone", (r"phone", r"mobile number", r"telephone")),
    ("linkedin", (r"linkedin",)),
    ("github", (r"github",)),
    ("website", (r"website", r"portfolio", r"personal site", r"other url",
                 r"\burl\b", r"web address")),
    ("location", (r"location", r"\bcity\b", r"where are you based",
                  r"country", r"address")),
    ("how_heard", (r"how did you hear", r"how you heard",
                   r"referral source", r"where did you (?:find|see)")),
)

_FULL_NAME_LABELS = frozenset({"name", "full name", "your name",
                               "legal name", "preferred name",
                               "full legal name"})

_WHY = re.compile(r"(?<![a-z])(why|interest|excite|motivat|drawn to)")


def _normalize(question) -> str:
    key = (question.key or "").replace("_", " ").replace("-", " ")
    return f"{question.label} {key}".lower().strip()


def slot_for(question) -> str:
    text = _normalize(question)
    bare = (question.label or "").strip().lower().rstrip("?:").strip()
    if bare in _FULL_NAME_LABELS:
        return "full_name"
    for slot, patterns in _SLOTS:
        for pattern in patterns:
            if re.search(r"(?<![a-z])" + pattern, text):
                return slot
    return ""


# --- contact details off the profile ---------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_SPLIT = re.compile(r"\s*[·•|]\s*|\s{2,}")


class Identity(BaseModel, frozen=True):
    full_name: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""


def _url(text: str) -> str:
    text = text.strip().strip("<>()[],")
    if text.startswith(("http://", "https://")):
        return text
    return f"https://{text}"


def identity_from(profile, legal) -> Identity:
    """Contacts live as a header line on the resume, not as fields."""
    name = (getattr(profile, "name", "") or "").strip()
    parts = name.split()
    email = phone = linkedin = github = website = ""
    location = (getattr(profile, "location", "") or "").strip()

    for line in getattr(profile, "contacts", ()) or ():
        for chunk in _SPLIT.split(line):
            chunk = chunk.strip()
            if not chunk:
                continue
            low = chunk.lower()
            if not email and (m := _EMAIL.search(chunk)):
                email = m.group(0)
            elif "linkedin.com" in low:
                linkedin = linkedin or _url(chunk)
            elif "github.com" in low:
                github = github or _url(chunk)
            elif low.startswith(("http://", "https://")) or (
                    "." in low and " " not in low and "@" not in low):
                website = website or _url(chunk)
            elif (m := _PHONE.search(chunk)) and \
                    sum(c.isdigit() for c in chunk) >= 7:
                phone = phone or m.group(0).strip()
            elif not location and any(c.isalpha() for c in chunk):
                location = chunk

    return Identity(
        full_name=name,
        first_name=parts[0] if parts else "",
        last_name=parts[-1] if len(parts) > 1 else "",
        email=email, phone=phone,
        location=location or (getattr(legal, "based_in_country", "") or ""),
        linkedin=linkedin, github=github, website=website,
    )


# --- prose matching ---------------------------------------------------

_STOPWORDS = frozenset(
    "a an and are as at be by do does for from have how in is it of on or "
    "please tell that the this to us was we what will with you your our "
    "would could describe about".split())


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) > 2 and t not in _STOPWORDS}


def best_pack_match(label: str, answers: dict[str, str],
                    floor: float = 0.45) -> tuple[str, str]:
    """The drafted (question, answer) for the same thing the form asks.

    Jaccard over content words with a floor: a near-miss match is worse
    than an empty field, because the empty field gets filled by a human
    who is looking at it.
    """
    want = _tokens(label)
    if not want:
        return "", ""
    best, score = ("", ""), 0.0
    for question, answer in (answers or {}).items():
        have = _tokens(question)
        if not have:
            continue
        overlap = len(want & have) / len(want | have)
        if overlap > score:
            best, score = (question, answer), overlap
    return best if score >= floor else ("", "")


def best_pack_answer(label: str, answers: dict[str, str],
                     floor: float = 0.45) -> str:
    return best_pack_match(label, answers, floor)[1]


def _choice(question, want_yes: bool) -> str:
    """Answer in the form's own wording: a select whose options read
    'Yes, I am authorized' must not be filled with a bare 'Yes'."""
    prefix = "yes" if want_yes else "no"
    for choice in question.choices or ():
        if choice.label.strip().lower().startswith(prefix):
            return choice.label
    return "Yes" if want_yes else "No"


def _comp_phrase(job_row: dict, target_comp_usd: int | None) -> tuple[str, str]:
    lo, hi = job_row.get("comp_min_usd"), job_row.get("comp_max_usd")
    if lo and hi:
        return (f"Aligned with the posted range for this role "
                f"(${lo:,}-${hi:,} USD).", "job")
    if lo:
        return (f"Aligned with the posted range for this role "
                f"(${lo:,}+ USD).", "job")
    if target_comp_usd:
        return (f"${target_comp_usd:,} USD base, open to discussing the "
                f"full package.", "config")
    return "", ""


_FEED_NAMES = {"remoteok": "RemoteOK", "himalayas": "Himalayas",
               "weworkremotely": "We Work Remotely", "remotive": "Remotive",
               "aijobs": "aijobs.net"}


def _how_heard(job_row: dict) -> str:
    source = (job_row.get("source") or "").lower()
    if source in _FEED_NAMES:
        return _FEED_NAMES[source]
    return "Company careers page"


def _document(documents: dict[str, str], *wants: str) -> str:
    """Prefer the ATS-safe .docx, then the PDF, then anything matching."""
    candidates = [(name, url) for name, url in (documents or {}).items()
                  if all(w in name.lower() for w in wants)]
    for suffix in (".docx", ".pdf", ".md"):
        for name, url in candidates:
            if name.lower().endswith(suffix):
                return url
    return candidates[0][1] if candidates else ""


def build_answer_sheet(
    *, job_row: dict, questions, profile, legal, pack,
    documents: dict[str, str] | None = None,
    target_comp_usd: int | None = None,
) -> AnswerSheet:
    """Every field on this job's form, answered from declared facts."""
    documents = dict(documents or {})
    identity = identity_from(profile, legal)
    auth = resolve_work_authorization(job_row, legal)
    answers: list[Answer] = []

    used: set[str] = set()
    for question in questions.questions:
        slot = slot_for(question)
        drafted = ""
        if not slot:
            matched, drafted = best_pack_match(question.label, pack.answers)
            if matched:
                used.add(matched)
        answers.append(_answer_one(
            question, slot, job_row=job_row, auth=auth, identity=identity,
            pack=pack, drafted=drafted, documents=documents,
            target_comp_usd=target_comp_usd))

    answers += _unplaced_pack_answers(pack, used, answers)

    needs_review = bool(auth.needs_review) or any(
        a.required and a.needs_input for a in answers)

    return AnswerSheet(
        job_id=int(job_row.get("id") or 0),
        company=job_row.get("company_display") or job_row.get("company") or "",
        title=job_row.get("title") or "",
        apply_url=job_row.get("apply_url") or "",
        ats=questions.ats, introspected=questions.introspected,
        question_note=questions.note, legal_note=auth.note,
        viable=bool(auth.viable), needs_review=needs_review,
        answers=tuple(answers), documents=documents,
    )


def _answer_one(question, slot: str, *, job_row: dict, auth,
                identity: Identity, pack, drafted: str,
                documents: dict[str, str],
                target_comp_usd: int | None) -> Answer:
    def made(value: str, source: str, note: str = "") -> Answer:
        return Answer(key=question.key, label=question.label,
                      kind=question.kind, value=value, source=source,
                      required=question.required,
                      needs_input=not value and source != "voluntary",
                      note=note)

    if slot == "voluntary":
        return Answer(
            key=question.key, label=question.label, kind=question.kind,
            source="voluntary", required=question.required,
            note="Voluntary self-identification - karani does not answer "
                 "these for you.")

    if slot == "resume":
        return made(_document(documents, "resume"), "document",
                    "" if documents else "No rendered resume in the "
                                         "artifact store - re-run draft.")
    if slot == "cover_letter":
        if question.kind == "file":
            return made(_document(documents, "cover", "letter"), "document")
        return made(pack.cover_letter, "pack")

    # --- attestations: resolved, never written (karani/legal.py) ---
    if slot == "work_auth":
        if auth.needs_review:
            return Answer(key=question.key, label=question.label,
                          kind=question.kind, source="legal",
                          required=question.required, needs_input=True,
                          note=auth.note)
        if auth.authorized_in_role_country is None:
            return made(auth.form_answers()["work_authorization"], "legal",
                        auth.note)
        return made(_choice(question, auth.authorized_in_role_country),
                    "legal", auth.note)
    if slot == "sponsorship":
        if auth.needs_review:
            return Answer(key=question.key, label=question.label,
                          kind=question.kind, source="legal",
                          required=question.required, needs_input=True,
                          note=auth.note)
        return made(_choice(question, auth.requires_sponsorship), "legal",
                    auth.note)
    if slot == "relocate":
        return made(_choice(question, auth.willing_to_relocate), "legal")
    if slot == "start":
        return made(auth.earliest_start, "legal")

    if slot == "full_name":
        return made(identity.full_name, "profile")
    if slot in ("first_name", "last_name", "email", "phone", "location",
                "linkedin", "github", "website"):
        return made(getattr(identity, slot), "profile")

    if slot == "salary":
        value, source = _comp_phrase(job_row, target_comp_usd)
        return made(value, source or "job",
                    "" if value else "No published band - decide your "
                                     "number before you submit.")
    if slot == "how_heard":
        return made(_how_heard(job_row), "job")

    # Free text: the drafter may already have answered this exact
    # question for this job (matched by the caller).
    if not drafted and question.kind in ("textarea", "text") and \
            _WHY.search(question.label or ""):
        drafted = pack.positioning
    return made(drafted, "pack")


def _unplaced_pack_answers(pack, used: set[str],
                           placed: list[Answer]) -> list[Answer]:
    """Drafted prose the form had no field for.

    The question set is often the fallback, and a company asks "tell us
    about a hard problem" in words the drafter never saw. Dropping
    those answers off the console would waste the one part of the pack
    that cost money to produce, so they ride along at the bottom.
    """
    extras: list[Answer] = []
    for index, (question, answer) in enumerate((pack.answers or {}).items()):
        if question in used or not answer:
            continue
        extras.append(Answer(
            key=f"pack_{index}", label=question, kind="textarea",
            value=answer, source="pack",
            note="Drafted for this job - no matching field on the form, "
                 "so check whether it is asked in different words."))
    if pack.cover_letter and not any(a.value == pack.cover_letter
                                     for a in placed):
        extras.append(Answer(
            key="cover_letter_text", label="Cover letter (text)",
            kind="textarea", value=pack.cover_letter, source="pack",
            note="For a form that wants the letter inline rather than "
                 "as a file."))
    return extras


__all__ = ["Answer", "AnswerSheet", "Identity", "best_pack_answer",
           "best_pack_match",
           "build_answer_sheet", "identity_from", "slot_for"]