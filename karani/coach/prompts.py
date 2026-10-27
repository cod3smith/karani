"""Coach prompts — two calls, one cached candidate prefix.

The stable/volatile split (ADR 0019) matters more here than anywhere
else in karani, because the stable half is the largest thing karani
owns: the resume, the merged profile core, the indexed records, and a
career transcript that can run to 12,000 words. All of it is identical
for every job, so it belongs in the system prompt where a provider can
cache it. Put one line of the job description above that line and the
cache never hits again — `tests/test_coach_prompts.py` asserts the
prefix is byte-identical across jobs.

Both calls share `candidate_prefix()` verbatim, so prepping two roles
in one pass pays the write once.
"""
from __future__ import annotations

# v3 (coach): prep moved off a single call onto a two-call pipeline —
#     STAR stories mined from the profile core and the career
#     transcript with verified provenance, derived comp research, a
#     refreshed dossier, and the candidate material moved into the
#     cached prefix. Supersedes drafting/prep.py's prep-v2.
PREP_PROMPT_VERSION = "prep-v3"
# The story-mining call is versioned separately: its output is
# persisted inside the pack and is the half most likely to be retuned.
STORY_PROMPT_VERSION = "star-v1"


def _persona() -> str:
    from karani.config import get_config
    p = get_config().positioning
    return (f"You are an interview coach for {p.candidate} based in "
            f"{p.based_in}, {p.narrative}.")


def candidate_prefix(*, resume: str = "", profile: str = "",
                     transcript: str = "", records: str = "") -> str:
    """Everything true of the candidate regardless of which job this is.

    Identical bytes for every role, so it sits in the system prompt and
    is cacheable. Adding anything job-specific here is a silent 10x
    cost regression, not a bug you will notice.
    """
    parts: list[str] = []
    if resume:
        parts.append(f"<resume>\n{resume}\n</resume>")
    if profile:
        parts.append(profile)
    if records:
        parts.append(
            "<indexed_records>\nThe candidate's positions and bullets with "
            "stable ids. Cite these ids — and only these — when you claim "
            "something the candidate did.\n\n"
            f"{records}\n</indexed_records>")
    if transcript:
        parts.append(
            "<career_transcript>\nThe candidate talking through their own "
            "career. Spoken, so it rambles; treat it as ground truth about "
            "their history and prefer its specifics over the resume's "
            "compressed phrasing. Never contradict the resume's dates or "
            f"titles.\n\n{transcript}\n</career_transcript>")
    return "\n\n".join(parts)


# --- call 1: mine STAR stories from the candidate's own material -----------

_STORY_RULES = """\

Your job: mine STAR stories from the candidate's OWN material for this
interview. You are not writing stories; you are finding the ones that
already exist in the records and shaping them for the questions this
role will ask.

Hard rules — these are enforced after you answer, and a story that
breaks them is deleted rather than corrected:

- Every story must cite its evidence. `source_ids` lists ids from
  <indexed_records>. `transcript_quote` is a VERBATIM span copied from
  <career_transcript> — not a paraphrase, not a tidy-up. A story with
  neither is discarded.
- An id that does not exist in <indexed_records> is discarded. Do not
  guess ids to make a story look sourced.
- Keep the numbers exactly as the source states them. If the record
  says 180ms, the story says 180ms.
- Do not merge two unrelated events into one story to make it stronger.
  A smaller true story beats a bigger assembled one; the candidate has
  to answer follow-up questions about it under scrutiny.
- 4-6 stories, each covering a different competency. Prefer stories
  where the candidate's own decision changed the outcome, and include
  at least one where something went wrong.

Each field is prose the candidate can say out loud:
`situation` 1-2 sentences, `task` 1 sentence, `action` 2-4 sentences
with the mechanism named, `result` 1-2 sentences with the figure.

Return a SINGLE JSON object and NOTHING else:

{
  "stories": [
    {"title": "<short handle the candidate will recognize>",
     "competencies": ["<what this story proves>"],
     "situation": "...", "task": "...", "action": "...", "result": "...",
     "source_ids": ["p0b1"],
     "transcript_quote": "<verbatim span, or empty>"}
  ]
}
"""

_STORY_USER = """\
<job>
<company>{company}</company>
<title>{title}</title>
<description>
{description}
</description>
</job>

<competencies_to_cover>
{competencies}
</competencies_to_cover>
"""


def story_system_prompt(**candidate) -> str:
    return _persona() + "\n\n" + _STORY_RULES + "\n\n" \
        + candidate_prefix(**candidate)


def story_user_prompt(job_row: dict, competencies: list[str] | None = None) -> str:
    return _STORY_USER.format(
        company=job_row.get("company_display") or job_row.get("company") or "",
        title=job_row.get("title", ""),
        description=(job_row.get("description_text") or "")[:8000],
        competencies="\n".join(f"- {c}" for c in (competencies or []))
        or "(derive them from the job description)",
    )


# --- call 2: the pack ------------------------------------------------------

_PREP_RULES = """\

Your job: assemble the prep pack the candidate reads the night before
this interview. The expensive research is already done and supplied
below — a refreshed company dossier, the fit analysis with its evidence
and gaps, verified STAR stories mined from the candidate's own records,
and resolved comp research. Your job is to connect them to this
conversation, not to repeat them.

Rules:
- `likely_questions`: 6-9. Derive at least half directly from the GAPS
  in the fit analysis — interviewers probe exactly what the materials
  do not prove. If the question bank has real past questions from this
  company, those come first. For each, `story_title` names the STAR
  story to answer with, chosen from <star_stories>; use "" only when no
  supplied story fits, and then say in `answer_angle` what the
  candidate should reach for instead. NEVER invent an experience to
  answer with — the stories are the only material you may cite.
- `questions_to_ask`: 4-6, and every one must cite something specific
  from the dossier or the job description in `source_basis` — a repo, a
  product, an architectural choice, a stated team problem. Generic
  questions are worthless. If the dossier is too thin, say so in
  `source_basis` and name what the candidate should look up by hand.
- `comp_notes`: 2-4 sentences on how to handle the money conversation
  in THIS interview, using only the figures in <comp_research>. Never
  state a salary number that is not in that block. If the posted band
  is absent, the advice is how to ask for it, not a guess at it.
- `warm_openers`: up to 3, for warm-path candidates named in the
  dossier. 1-2 honest sentences leading with shared technical ground.
  No flattery and no pretending to know them.
- `company_brief`: 4-8 sentences the candidate can absorb in one read —
  what they build, stage and scale, engineering-culture signals,
  anything recent. Only facts from the dossier; never invent a product,
  a funding round or a blog post.
- `red_flags`: anything in the dossier or the posting the candidate
  should walk in already knowing. Empty is a valid answer.

Return a SINGLE JSON object matching the schema and NOTHING else:

{
  "company_brief": "<4-8 sentences>",
  "likely_questions": [
    {"question": "...", "why": "<which gap or signal triggers it>",
     "story_title": "<a title from <star_stories>, or empty>",
     "answer_angle": "<2-3 sentences on how to land it>"}
  ],
  "questions_to_ask": [
    {"question": "...", "source_basis": "<the dossier fact it builds on>"}
  ],
  "comp_notes": "<2-4 sentences, figures only from <comp_research>>",
  "warm_openers": [{"contact": "<login/name>", "opener": "<1-2 sentences>"}],
  "red_flags": ["..."],
  "positioning_reminder": "<2-3 sentences: the angle to lead with>"
}
"""

_PREP_USER = """\
<job>
<company>{company}</company>
<title>{title}</title>
<stage>{stage}</stage>
<description>
{description}
</description>
</job>

<fit_analysis>
{qualification}
</fit_analysis>

<company_dossier>
{dossier}
</company_dossier>

<star_stories>
Verified against the candidate's own records. These are the only
experiences you may cite; each is named by its title.

{stories}
</star_stories>

<comp_research>
Resolved from the posting, the candidate's declared floor and target,
and karani's own ingestion. Use these figures verbatim; originate none.

{comp}
</comp_research>

<question_bank>
{question_bank}
</question_bank>
"""


def prep_system_prompt(**candidate) -> str:
    return _persona() + "\n\n" + _PREP_RULES + "\n\n" \
        + candidate_prefix(**candidate)


def prep_user_prompt(*, job_row: dict, stage: str = "interview",
                     qualification: str = "", dossier: str = "",
                     stories: str = "", comp: str = "",
                     question_bank: list[str] | None = None) -> str:
    return _PREP_USER.format(
        company=job_row.get("company_display") or job_row.get("company") or "",
        title=job_row.get("title", ""),
        stage=stage,
        description=(job_row.get("description_text") or "")[:8000],
        qualification=qualification or "(no qualification on file)",
        dossier=dossier or "(no dossier gathered)",
        stories=stories or "(no verified stories — do not invent any)",
        comp=comp or "(no comp research available)",
        question_bank="\n".join(f"- {q}" for q in (question_bank or []))
        or "(no past questions recorded for this company)",
    )
