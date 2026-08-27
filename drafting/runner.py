"""Drafting runner: one job → LLM → DraftPackage → markdown file."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from qualification.client import QualifierClient, _extract_json
from qualification.models import QualificationResult

from .models import DraftPackage
from .prompts import DRAFT_PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from .writers import write_markdown

log = logging.getLogger(__name__)


async def draft_for_job(
    client: QualifierClient,
    *,
    resume: str,
    job_row: dict,
    qualification: QualificationResult | dict | None = None,
    output_path: str | Path | None = None,
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

    user = build_user_prompt(resume=resume, qualification=qual_text, job_row=job_row)
    raw = await client.complete(SYSTEM_PROMPT, user)

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

    path = write_markdown(pkg, job_row, output_path=output_path)
    return pkg, path
