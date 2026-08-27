"""Interview prep pack — company brief, gap-derived questions, questions
to ask grounded in company data, and warm-path openers.

The qualifier's evidence-backed `gaps` are exactly what interviewers
probe; the company dossier (intel/) is what makes questions-to-ask
specific instead of generic. Both feed one prompt, one JSON package,
one markdown file in drafts/.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from qualification.client import QualifierClient, _extract_json

log = logging.getLogger(__name__)

PREP_PROMPT_VERSION = "prep-v1"

SYSTEM_PROMPT = """\
You are an interview-prep copilot for a senior/staff engineer (Nairobi-based,
interviewing at globally-distributed companies paying SF bands).

Given the job, the candidate's resume, the fit analysis (including GAPS —
the requirements the candidate doesn't obviously meet), a company dossier
built from public sources, and a bank of questions this company has asked
before, produce a prep pack.

Rules:
- `likely_questions`: 5-8. Derive at least half directly from the GAPS —
  interviewers probe exactly what the resume doesn't prove. Each suggested
  answer must draw on REAL resume material (STAR-shaped where relevant);
  never invent experience. If the question bank has real past questions,
  include them first.
- `questions_to_ask`: 3-5, and every one must cite something specific from
  the dossier (a repo, a blog topic, a product, a background fact). Generic
  questions ("what's the culture like?") are worthless — if the dossier is
  too thin for a specific question, say so in `source_basis` and propose
  what to research manually.
- `warm_openers`: for up to 3 warm-path candidates from the dossier, a
  1-2 sentence honest opener the candidate could send. No flattery, no
  pretending to know them; lead with genuine shared technical ground.
- `company_brief`: 4-8 sentences a candidate can absorb in one read:
  what they do, stage/scale, engineering culture signals, anything recent.

Return a SINGLE JSON object matching the schema and NOTHING else:

{
  "company_brief": "<4-8 sentences>",
  "likely_questions": [
    {"question": "...", "why": "<which gap/signal triggers it>",
     "suggested_answer": "<STAR-shaped, from real resume material>"}
  ],
  "questions_to_ask": [
    {"question": "...", "source_basis": "<the dossier fact it builds on>"}
  ],
  "warm_openers": [
    {"contact": "<login/name>", "opener": "<1-2 sentences>"}
  ],
  "positioning_reminder": "<2-3 sentences>"
}
"""

USER_PROMPT_TEMPLATE = """\
<resume>
{resume}
</resume>

<job>
<company>{company}</company>
<title>{title}</title>
<description>
{description}
</description>
</job>

<qualification_analysis>
{qualification}
</qualification_analysis>

<company_dossier>
{dossier}
</company_dossier>

<question_bank>
{question_bank}
</question_bank>
"""


class LikelyQuestion(BaseModel):
    question: str
    why: str = ""
    suggested_answer: str = ""


class QuestionToAsk(BaseModel):
    question: str
    source_basis: str = ""


class WarmOpener(BaseModel):
    contact: str
    opener: str


class PrepPackage(BaseModel):
    company_brief: str = ""
    likely_questions: list[LikelyQuestion] = Field(default_factory=list)
    questions_to_ask: list[QuestionToAsk] = Field(default_factory=list)
    warm_openers: list[WarmOpener] = Field(default_factory=list)
    positioning_reminder: str = ""
    # Provenance
    model: str = ""
    prompt_version: str = ""
    job_id: int = 0


def render_markdown(pkg: PrepPackage, job_row: dict) -> str:
    company = job_row.get("company_display") or job_row.get("company") or ""
    lines = [
        f"# Interview prep — {company} · {job_row.get('title', '')}",
        "",
        f"- **Job id:** {pkg.job_id}",
        f"- **Model:** {pkg.model} ({pkg.prompt_version})",
        "",
        "## Company brief", "", pkg.company_brief, "",
        "## Likely questions (prep these)", "",
    ]
    for q in pkg.likely_questions:
        lines += [f"### {q.question}", ""]
        if q.why:
            lines.append(f"_Why they'll ask: {q.why}_")
        if q.suggested_answer:
            lines += ["", q.suggested_answer]
        lines.append("")
    lines += ["## Questions to ask them", ""]
    for q in pkg.questions_to_ask:
        lines.append(f"- {q.question}")
        if q.source_basis:
            lines.append(f"  _basis: {q.source_basis}_")
    if pkg.warm_openers:
        lines += ["", "## Warm-path openers", ""]
        for w in pkg.warm_openers:
            lines.append(f"- **{w.contact}**: {w.opener}")
    if pkg.positioning_reminder:
        lines += ["", "## Positioning reminder", "", pkg.positioning_reminder]
    return "\n".join(lines)


def default_path(job_row: dict, root: str = "drafts") -> Path:
    from .writers import _slug
    jid = job_row.get("id", "?")
    company = _slug(job_row.get("company_display")
                    or job_row.get("company") or "co")
    return Path(root) / f"prep_{jid:0>5}_{company}.md"


async def prep_for_job(
    client: QualifierClient,
    *,
    resume: str,
    job_row: dict,
    qualification: dict | str | None = None,
    dossier: str = "",
    question_bank: list[str] | None = None,
    output_path: str | Path | None = None,
) -> tuple[PrepPackage, Path]:
    qual_text = (json.dumps(qualification, indent=2, default=str)
                 if isinstance(qualification, dict)
                 else (qualification or "(no qualification on file)"))
    user = USER_PROMPT_TEMPLATE.format(
        resume=resume,
        company=job_row.get("company_display") or job_row.get("company") or "",
        title=job_row.get("title", ""),
        description=(job_row.get("description_text") or "")[:8000],
        qualification=qual_text,
        dossier=dossier or "(no dossier)",
        question_bank="\n".join(f"- {q}" for q in (question_bank or []))
        or "(no past questions recorded)",
    )
    raw = await client.complete(SYSTEM_PROMPT, user)
    try:
        pkg = PrepPackage.model_validate(_extract_json(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("prep returned malformed JSON: %s -- raw: %r", e, raw[:400])
        pkg = PrepPackage(company_brief=(
            "PREP FAILED: the LLM did not return valid JSON. "
            "Try again or with a different model."
        ))
    pkg.model = getattr(client, "model_name", "unknown")
    pkg.prompt_version = PREP_PROMPT_VERSION
    pkg.job_id = int(job_row.get("id") or 0)

    path = Path(output_path) if output_path else default_path(job_row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(pkg, job_row), encoding="utf-8")
    return pkg, path
