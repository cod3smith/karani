"""Follow-up notes for applications gone quiet.

`next_actions.follow_up` says who is due; this writes the note. The rule
that makes follow-ups work: reference something NEW and specific (a
release, a blog post, a fact from the company dossier) — a bare "just
checking in" is worse than silence.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ValidationError

from karani.qualification.client import QualifierClient, _extract_json

log = logging.getLogger(__name__)

FOLLOWUP_PROMPT_VERSION = "followup-v1"  # persona-free; no bump needed

SYSTEM_PROMPT = """\
You write short follow-up notes for a senior/staff engineer whose job
application has gone unanswered.

Rules:
- 60-120 words. Three beats: (1) one specific, genuine hook from the
  company dossier — a repo, a launch, a blog topic; (2) a one-line
  restatement of the strongest fit signal from the original application;
  (3) a low-pressure close with a concrete next step.
- Never apologize for following up, never guilt-trip, never "just
  checking in" or "bumping this".
- If the dossier offers no specific hook, open with the fit signal
  instead and set "hook_used" to "".

Return a SINGLE JSON object and NOTHING else:

{
  "note": "<60-120 words>",
  "subject_line": "<for email>",
  "hook_used": "<the dossier fact referenced, or empty>"
}
"""

USER_PROMPT_TEMPLATE = """\
<application>
<company>{company}</company>
<title>{title}</title>
<applied_days_ago>{days}</applied_days_ago>
<original_positioning>{positioning}</original_positioning>
</application>

<company_dossier>
{dossier}
</company_dossier>
"""


class FollowUpNote(BaseModel):
    note: str
    subject_line: str = ""
    hook_used: str = ""
    # Provenance
    model: str = ""
    prompt_version: str = ""
    job_id: int = 0


def default_path(job_row: dict, root: str = "drafts") -> Path:
    from .writers import _slug
    jid = job_row.get("id", "?")
    company = _slug(job_row.get("company_display")
                    or job_row.get("company") or "co")
    return Path(root) / f"followup_{jid:0>5}_{company}.md"


def render_markdown(pkg: FollowUpNote, job_row: dict) -> str:
    company = job_row.get("company_display") or job_row.get("company") or ""
    lines = [
        f"# Follow-up — {company} · {job_row.get('title', '')}",
        "",
        f"- **Job id:** {pkg.job_id}",
        f"- **Model:** {pkg.model} ({pkg.prompt_version})",
        f"- **Subject:** {pkg.subject_line}" if pkg.subject_line else "",
        f"- **Hook:** {pkg.hook_used}" if pkg.hook_used else "",
        "",
        pkg.note.strip(),
    ]
    return "\n".join(line for line in lines if line is not None)


async def followup_for_job(
    client: QualifierClient,
    *,
    job_row: dict,
    days_since_applied: int,
    positioning: str = "",
    dossier: str = "",
    output_path: str | Path | None = None,
) -> tuple[FollowUpNote, Path]:
    user = USER_PROMPT_TEMPLATE.format(
        company=job_row.get("company_display") or job_row.get("company") or "",
        title=job_row.get("title", ""),
        days=days_since_applied,
        positioning=positioning or "(not recorded)",
        dossier=dossier or "(no dossier)",
    )
    raw = await client.complete(SYSTEM_PROMPT, user)
    try:
        pkg = FollowUpNote.model_validate(_extract_json(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("followup returned malformed JSON: %s -- raw: %r",
                    e, raw[:400])
        pkg = FollowUpNote(note=(
            "FOLLOW-UP FAILED: the LLM did not return valid JSON. "
            "Try again or write it manually."
        ))
    pkg.model = getattr(client, "model_name", "unknown")
    pkg.prompt_version = FOLLOWUP_PROMPT_VERSION
    pkg.job_id = int(job_row.get("id") or 0)

    path = Path(output_path) if output_path else default_path(job_row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(pkg, job_row), encoding="utf-8")
    return pkg, path
