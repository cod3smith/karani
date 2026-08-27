"""Turn a DraftPackage into a markdown file on disk."""
from __future__ import annotations

import re
from pathlib import Path

from .models import DraftPackage


def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:max_len] or "role"


def default_path(job_row: dict, root: str = "drafts") -> Path:
    company = _slug(job_row.get("company_display") or job_row.get("company") or "co")
    title = _slug(job_row.get("title") or "role")
    jid = job_row.get("id", "?")
    return Path(root) / f"{jid:0>5}_{company}__{title}.md"


def _word_count(text: str) -> int:
    return len((text or "").split())


def render_markdown(pkg: DraftPackage, job_row: dict) -> str:
    company = job_row.get("company_display") or job_row.get("company") or ""
    title = job_row.get("title") or ""
    apply_url = job_row.get("apply_url") or ""

    lines: list[str] = [
        f"# Application draft — {company} · {title}",
        f"",
        f"- **Apply:** {apply_url}" if apply_url else "",
        f"- **Job id:** {pkg.job_id}" if pkg.job_id else "",
        f"- **Verdict at draft:** {pkg.verdict_at_draft}",
        f"- **Draft model:** {pkg.model} ({pkg.prompt_version})",
    ]
    if pkg.tone_note:
        lines.append(f"- **Tone:** _{pkg.tone_note}_")
    lines.append("")

    if pkg.positioning_summary:
        lines += ["## Positioning", "", pkg.positioning_summary, ""]

    if pkg.subject_line:
        lines += ["## Suggested subject line", "", f"> {pkg.subject_line}", ""]

    lines += [
        "## Cover letter",
        "",
        pkg.cover_letter.strip(),
        "",
        f"_{_word_count(pkg.cover_letter)} words_",
        "",
    ]

    if pkg.tailored_bullets:
        lines += ["## Tailored resume bullets", ""]
        for b in pkg.tailored_bullets:
            lines.append(f"- **{b.original_role}** — {b.text}")
            if b.why_this_bullet:
                lines.append(f"  _{b.why_this_bullet}_")
        lines.append("")

    if pkg.application_answers:
        lines += ["## Application question answers", ""]
        for a in pkg.application_answers:
            wc = a.word_count or _word_count(a.answer)
            lines += [
                f"### {a.question}",
                "",
                a.answer.strip(),
                "",
                f"_{wc} words_",
                "",
            ]

    return "\n".join(l for l in lines if l is not None)


def write_markdown(pkg: DraftPackage, job_row: dict,
                   output_path: str | Path | None = None) -> Path:
    path = Path(output_path) if output_path else default_path(job_row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(pkg, job_row), encoding="utf-8")
    return path
