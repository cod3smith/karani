"""Drafting runner: one job → LLM → DraftPackage → markdown file."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from karani.qualification.client import QualifierClient, _extract_json
from karani.qualification.models import QualificationResult

from .keywords import coverage, extract_keywords
from .models import DraftPackage
from .prompts import DRAFT_PROMPT_VERSION, build_user_prompt, system_prompt
from .writers import write_markdown

log = logging.getLogger(__name__)


async def draft_for_job(
    client: QualifierClient,
    *,
    resume: str,
    job_row: dict,
    qualification: QualificationResult | dict | None = None,
    output_path: str | Path | None = None,
    storage=None,
) -> tuple[DraftPackage, Path]:
    """Generate + persist a draft package for a single job."""
    qual_text = ""
    if isinstance(qualification, QualificationResult):
        qual_text = qualification.model_dump_json(indent=2)
        verdict = qualification.verdict
    elif isinstance(qualification, dict):
        qual_text = json.dumps(qualification, indent=2)
        verdict = qualification.get("verdict", "unknown")
    else:
        verdict = "unknown"

    # ATS keyword pass: which JD terms does the resume alone miss? The
    # drafter is asked to work those in honestly (see prompts.py rules).
    jd_keywords = extract_keywords(job_row.get("description_text") or "")
    resume_gap = coverage(jd_keywords, resume)

    # Legal answers are resolved from declared facts, never left to the
    # model (karani/legal.py).
    from karani.config import get_config
    from karani.legal import resolve_work_authorization
    work_auth = resolve_work_authorization(job_row, get_config().legal)

    # Company dossier (cached 14d) — the material that lets the letter
    # say something true and specific about THIS company rather than
    # praising its "culture". Best-effort: an unknown company just
    # yields an empty block (draft-v5 tells the model not to invent).
    dossier = ""
    company = job_row.get("company_display") or job_row.get("company") or ""
    if storage is not None and company:
        try:
            from karani.intel.service import dossier_text, get_company_intel
            dossier = dossier_text(await get_company_intel(storage, company))
        except Exception:
            log.warning("company intel unavailable for %s; drafting without "
                        "a dossier", company, exc_info=True)

    from karani.candidate import candidate_context
    ctx = candidate_context(resume)
    user = build_user_prompt(resume=resume, qualification=qual_text,
                             job_row=job_row,
                             keyword_targets=resume_gap["missing"],
                             work_auth=work_auth, dossier=dossier,
                             experience=ctx["experience"],
                             profile=ctx["profile"])
    raw = await client.complete(system_prompt(), user)

    try:
        data = _extract_json(raw)
        pkg = DraftPackage.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("draft returned malformed JSON: %s -- raw: %r", e, raw[:400])
        pkg = DraftPackage(
            cover_letter=(
                "DRAFTING FAILED: the LLM did not return valid JSON. "
                "Try again with a different model, or draft manually."
            ),
            tone_note="",
        )

    pkg.model = getattr(client, "model_name", "unknown")
    pkg.prompt_version = DRAFT_PROMPT_VERSION
    pkg.job_id = int(job_row.get("id") or 0)
    pkg.verdict_at_draft = verdict if verdict in {"qualified", "maybe", "skip"} else "unknown"

    # Final coverage: resume + everything the draft adds. Persisted per
    # application so funnel_stats can correlate coverage with responses.
    materials = "\n".join([
        resume, pkg.cover_letter,
        *(b.text for b in pkg.tailored_bullets),
        *(a.answer for a in pkg.application_answers),
    ])
    final = coverage(jd_keywords, materials)
    pkg.keyword_coverage = final["score"]
    pkg.keyword_missing = final["missing"]

    path = write_markdown(pkg, job_row, output_path=output_path)
    return pkg, path
