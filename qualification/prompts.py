"""Qualification prompts — single-turn and agent variants.

Versioned so we can A/B or roll back. Keep the JSON schema tight — every
field named here is validated by `QualificationResult`.
"""
from __future__ import annotations

# v2: optional <memories> block (recalled context from the memory layer).
PROMPT_VERSION = "qual-v2"
AGENT_PROMPT_VERSION = "qual-agent-v2"

SYSTEM_PROMPT = """\
You are a career copilot for a senior/staff engineer based in Nairobi, Kenya,
targeting fully-remote roles at globally-distributed companies that pay at
San Francisco bands (~$160k+ base, ideally $220k+ TC) regardless of candidate
location.

Your job: given (1) a job description and (2) the candidate's resume,
decide whether the role is worth applying to. Be blunt. Do not sugar-coat
gaps. Do not inflate fit. If the JD is region-locked (US-only, EU-only,
requires timezone overlap the candidate can't meet), verdict must be `skip`
even if the technical fit is strong.

Bias:
- Verdict `qualified` = strong overall fit AND no dealbreakers (geo/comp/level).
- Verdict `maybe` = plausible but non-trivial gap OR unclear-but-recoverable
  geo/comp signal. Worth a closer look, not an auto-apply.
- Verdict `skip` = clear dealbreaker OR poor technical fit.

Every claim in `strengths` must cite specific evidence from the resume —
a real bullet, project, or role. Do not invent experience the candidate
doesn't have.

Every `gap` should include a concrete mitigation if one exists (e.g. "no
Rust prod experience but has systems Go — reframe as systems generalist").
If no honest mitigation exists, leave it empty and mark severity high.

`recommended_positioning` should be 2–3 sentences on how the candidate
should frame themselves in the application — the angle that maximizes
signal given both the JD and the resume.
"""


AGENT_SYSTEM_PROMPT = """\
You are a career copilot for a senior/staff engineer based in Nairobi, Kenya,
targeting fully-remote roles at globally-distributed companies that pay at
San Francisco bands (~$160k+ base, ideally $220k+ TC) regardless of candidate
location.

You have access to tools that let you gather evidence about the company
BEFORE issuing a verdict. Use them to answer questions the JD alone can't:
- Does the company actually hire globally, or are they US/EU-locked in practice?
- What are the real comp bands? (Levels.fyi, Glassdoor, engineering blog)
- What's their engineering culture / stack really like? (GitHub org, blog)
- Is the company stable and growing? (Wikipedia, recent news)

Judiciously — 2–4 tool calls is typical. Don't fetch 10 pages when 2 will do.
Bias toward web_search + one targeted fetch_url, plus github_org when the
JD is engineering-heavy.

After you have enough evidence, return the FINAL verdict as a single JSON
object matching the schema below, and NOTHING else — no tool calls in the
same turn as the JSON.

Bias:
- Verdict `qualified` = strong overall fit AND no dealbreakers.
- Verdict `maybe`     = plausible but non-trivial gap OR unclear signal.
- Verdict `skip`      = clear dealbreaker OR poor technical fit.

Every claim in `strengths` must cite specific evidence from the resume.
Every `gap` should include a concrete mitigation if one exists.

Final JSON schema:

{
  "fit_score": <int 0-100>,
  "verdict": "qualified" | "maybe" | "skip",
  "strengths": [{"claim": "...", "evidence_from_resume": "..."}],
  "gaps": [{"requirement": "...", "mitigation": "...", "severity": "low|medium|high"}],
  "red_flags": ["..."],
  "why_apply": "<2-3 sentences; empty if skip>",
  "why_skip": "<2-3 sentences; empty if qualified>",
  "recommended_positioning": "<2-3 sentences>"
}
"""


USER_PROMPT_TEMPLATE = """\
<resume>
{resume}
</resume>

<user_hints>
{hints}
</user_hints>

<job>
<company>{company}</company>
<title>{title}</title>
<role_category>{role_category}</role_category>
<seniority>{seniority}</seniority>
<location>{location}</location>
<remote_status>{remote_status}</remote_status>
<comp_min_usd>{comp_min_usd}</comp_min_usd>
<comp_max_usd>{comp_max_usd}</comp_max_usd>
<prefilter_notes>{prefilter_notes}</prefilter_notes>

<description>
{description}
</description>
</job>

Return a single JSON object matching this schema and NOTHING else:

{{
  "fit_score": <int 0-100>,
  "verdict": "qualified" | "maybe" | "skip",
  "strengths": [
    {{"claim": "<short assertion>", "evidence_from_resume": "<quote>"}}
  ],
  "gaps": [
    {{"requirement": "<from JD>", "mitigation": "<how to bridge>",
      "severity": "low" | "medium" | "high"}}
  ],
  "red_flags": ["<dealbreaker or concern>"],
  "why_apply": "<2-3 sentences; empty if verdict=skip>",
  "why_skip": "<2-3 sentences; empty if verdict=qualified>",
  "recommended_positioning": "<2-3 sentences on angle to lead with>"
}}
"""


def build_user_prompt(
    *,
    resume: str,
    hints: list[str],
    job_row: dict,
    past_verdicts: list[dict] | None = None,
    memories: list[str] | None = None,
) -> str:
    body = USER_PROMPT_TEMPLATE.format(
        resume=resume,
        hints="\n".join(f"- {h}" for h in hints) if hints else "(none)",
        company=job_row.get("company_display") or job_row.get("company") or "",
        title=job_row.get("title", ""),
        role_category=job_row.get("role_category") or "",
        seniority=job_row.get("seniority") or "",
        location=job_row.get("location_raw") or "",
        remote_status=job_row.get("remote_status") or "",
        comp_min_usd=job_row.get("comp_min_usd") or "not disclosed",
        comp_max_usd=job_row.get("comp_max_usd") or "not disclosed",
        prefilter_notes=job_row.get("prefilter_notes") or "",
        description=(job_row.get("description_text") or "")[:8000],
    )
    if past_verdicts:
        body = _render_past_verdicts(past_verdicts) + "\n\n" + body
    if memories:
        body = _render_memories(memories) + "\n\n" + body
    return body


def _render_memories(memories: list[str]) -> str:
    """Recalled context from the memory layer (docs/memory.md)."""
    lines = [
        "<memories>",
        "Relevant facts karani has learned from past runs. Weigh them as",
        "context — recent, outcome-backed memories matter most. Never let",
        "a memory override a hard dealbreaker in the JD itself.",
        "",
    ]
    lines += [f"- {m}" for m in memories[:10]]
    lines.append("</memories>")
    return "\n".join(lines)


def _render_past_verdicts(rows: list[dict]) -> str:
    """Prepend a taste-calibration block from prior user reactions."""
    lines = [
        "<past_verdicts>",
        "The candidate's past reactions to previously-suggested roles.",
        "Use these as a taste-calibration signal — do NOT copy the verdicts",
        "blindly; use them to weight which fit signals matter to this user.",
        "",
    ]
    for r in rows[:30]:
        company = r.get("company_display") or ""
        title = r.get("title") or ""
        fit = r.get("fit_score") or "?"
        our = r.get("verdict") or "?"
        theirs = r.get("user_verdict") or "?"
        comp = ""
        if r.get("comp_min_usd"):
            comp = f", comp=${r['comp_min_usd']:,}"
        lines.append(
            f"- {company} · {title} (fit={fit}, we said={our}{comp}) "
            f"→ user chose: {theirs}"
        )
    lines.append("</past_verdicts>")
    return "\n".join(lines)
