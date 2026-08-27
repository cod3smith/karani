"""Full tailored resume per job — not just bullets, the whole document.

Takes the master resume + JD + qualification + keyword targets and emits
a complete resume markdown reshaped for THIS role: summary rewritten to
the JD's language, experience reordered by relevance, keyword targets
worked in only where the master resume gives honest grounds. Same facts,
different emphasis — inventing experience is forbidden by prompt and by
the whole point.

Anti-AI-voice rules are baked into this prompt (rather than running the
humanizer as a second pass over a full resume) to keep it one call.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ValidationError

from qualification.client import QualifierClient, _extract_json

from .humanize import AI_TELLS
from .keywords import coverage, extract_keywords

log = logging.getLogger(__name__)

RESUME_PROMPT_VERSION = "resume-v1"

SYSTEM_PROMPT = """\
You tailor a senior engineer's resume for one specific role.

Rules of substance:
- Every fact, employer, date, title, and number comes from the master
  resume. NEVER invent, inflate, or extend experience. If the JD wants
  something the resume can't honestly support, leave it out.
- Reorder and reweight: the experiences and bullets most relevant to
  this JD come first and get the most space. Trim what doesn't serve
  this application.
- Rewrite the summary/headline for this role using the JD's own
  vocabulary where honest.
- Work the given keyword targets in ONLY where the resume gives real
  grounds.

Rules of voice (this must read human-written):
- Plain and direct. No {tells}.
- No "not only ... but also". At most one em-dash per section.
- Bullets lead with the concrete outcome and keep the numbers.

Return a SINGLE JSON object and NOTHING else:

{{
  "resume_markdown": "<the complete tailored resume as markdown>",
  "changes_summary": "<3-5 bullets on what was reshaped and why>"
}}
"""

USER_PROMPT_TEMPLATE = """\
<master_resume>
{resume}
</master_resume>

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

<keyword_targets>
{keyword_targets}
</keyword_targets>
"""


class TailoredResume(BaseModel):
    resume_markdown: str
    changes_summary: str = ""
    # Provenance
    model: str = ""
    prompt_version: str = ""
    job_id: int = 0
    keyword_coverage: float | None = None


def default_path(job_row: dict, root: str = "drafts") -> Path:
    from .writers import _slug
    jid = job_row.get("id", "?")
    company = _slug(job_row.get("company_display")
                    or job_row.get("company") or "co")
    return Path(root) / f"resume_{jid:0>5}_{company}.md"


async def tailor_resume(
    client: QualifierClient,
    *,
    resume: str,
    job_row: dict,
    qualification: dict | str | None = None,
    output_path: str | Path | None = None,
) -> tuple[TailoredResume, Path]:
    jd = job_row.get("description_text") or ""
    jd_keywords = extract_keywords(jd)
    targets = coverage(jd_keywords, resume)["missing"]

    system = SYSTEM_PROMPT.format(tells=", ".join(AI_TELLS[:16]))
    user = USER_PROMPT_TEMPLATE.format(
        resume=resume,
        company=job_row.get("company_display") or job_row.get("company") or "",
        title=job_row.get("title", ""),
        description=jd[:8000],
        qualification=(json.dumps(qualification, indent=2, default=str)
                       if isinstance(qualification, dict)
                       else (qualification or "(none)")),
        keyword_targets="\n".join(f"- {t}" for t in targets) or "(none)",
    )
    raw = await client.complete(system, user)
    try:
        result = TailoredResume.model_validate(_extract_json(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("resume tailor returned malformed JSON: %s", e)
        result = TailoredResume(
            resume_markdown="",
            changes_summary="TAILORING FAILED: malformed LLM output.",
        )
    result.model = getattr(client, "model_name", "unknown")
    result.prompt_version = RESUME_PROMPT_VERSION
    result.job_id = int(job_row.get("id") or 0)
    if result.resume_markdown:
        result.keyword_coverage = coverage(
            jd_keywords, result.resume_markdown)["score"]

    path = Path(output_path) if output_path else default_path(job_row)
    if result.resume_markdown:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.resume_markdown, encoding="utf-8")
    return result, path
