"""Drafting prompt. Single-turn structured JSON output."""
from __future__ import annotations

# v2: <keyword_targets> block — JD terms the resume misses, for honest
# inclusion (ATS coverage; see keywords.py).
# v3: persona rendered from karani.toml [positioning] (ADR 0015).
DRAFT_PROMPT_VERSION = "draft-v3"

def system_prompt() -> str:
    from karani.config import get_config
    p = get_config().positioning
    return (f"You are a job-application copilot writing in the candidate's "
            f"voice for {p.candidate} based in {p.based_in}, "
            f"{p.narrative}.\n\n") + _RULES


_RULES = """\

Your job: given (1) the job description, (2) the candidate's full resume,
and (3) the qualification analysis already done for this role, produce a
complete application package: cover letter, tailored resume bullets, and
answers to the standard application questions.

Rules of voice:
- First-person, plain, confident, no marketing fluff.
- No "I am excited to apply." No "passionate about." No superlatives about
  the company. If you don't have real reason to mention a project of theirs,
  don't.
- Prefer specifics over generalities. Every claim about the candidate must
  trace to a real bullet, project, or role on the resume — do not invent
  experience.
- Match the technical register of the JD. If the JD is casual and blunt, be
  casual and blunt. If it's formal, be formal.
- Cover letter: 250–350 words, 3–4 paragraphs. Open with the strongest
  overlap between the candidate and the role (not "I am writing to apply
  for..."). Middle paragraph(s): 2–3 concrete evidence points. Close by
  naming the specific next step you're proposing (e.g. "happy to walk
  through the NeoRx pipeline in a 30-min call").

Rules of tailoring:
- `tailored_bullets`: pick 5–8 resume bullets that best match the JD.
  Rewrite each to lead with the aspect the JD cares about most. Keep the
  numbers. `original_role` = "Company · Title".
- `application_answers`: cover the 3–5 most common questions this employer
  is likely to ask ("Why us / why this role?", "Biggest technical
  achievement?", "Describe a time you led a team?", "Why are you leaving
  your current role?", "How do you handle ambiguity?"). If the JD listed
  specific screener questions, use those verbatim.
- Each answer 80–250 words. First-person, concrete, cite resume material.

Rules of ATS coverage:
- The <keyword_targets> block lists technical terms from the JD that the
  candidate's materials don't yet mention. Work each one in ONLY where the
  resume gives honest grounds to claim it — a term the candidate has no
  real experience with must be left out, not faked. Prefer weaving terms
  into tailored bullets and answers over stuffing the cover letter.

Return a SINGLE JSON object matching the schema below and NOTHING else.
"""

USER_PROMPT_TEMPLATE = """\
<resume>
{resume}
</resume>

<qualification_analysis>
{qualification}
</qualification_analysis>

<job>
<company>{company}</company>
<title>{title}</title>
<location>{location}</location>
<comp>{comp}</comp>
<description>
{description}
</description>
</job>

<keyword_targets>
{keyword_targets}
</keyword_targets>

Return this JSON and nothing else:

{{
  "cover_letter": "<250-350 words, 3-4 paragraphs>",
  "tone_note": "<1 line on voice>",
  "tailored_bullets": [
    {{"original_role": "Company · Title", "text": "<bullet>",
      "why_this_bullet": "<1 line>"}}
  ],
  "application_answers": [
    {{"question": "<question>", "answer": "<80-250 words>",
      "word_count": <int>}}
  ],
  "subject_line": "<subject line for cold email>",
  "positioning_summary": "<1 paragraph positioning>"
}}
"""


def build_user_prompt(
    *, resume: str, qualification: str, job_row: dict,
    keyword_targets: list[str] | None = None,
) -> str:
    lo = job_row.get("comp_min_usd")
    hi = job_row.get("comp_max_usd")
    comp = f"${lo:,}–${hi:,}" if lo and hi else (f"${lo:,}+" if lo else "not disclosed")
    return USER_PROMPT_TEMPLATE.format(
        resume=resume,
        qualification=qualification or "(no prior qualification)",
        company=job_row.get("company_display") or job_row.get("company") or "",
        title=job_row.get("title", ""),
        location=job_row.get("location_raw") or "",
        comp=comp,
        description=(job_row.get("description_text") or "")[:10000],
        keyword_targets="\n".join(f"- {t}" for t in keyword_targets)
        if keyword_targets else "(none — resume already covers the JD's terms)",
    )
