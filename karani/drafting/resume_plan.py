"""Tailoring as selection over records, with a traceable journey.

The prose tailorer handed a model the whole resume and took back a
whole resume. That works, but nothing downstream can say where an
output bullet came from, and the facts survive only because the prompt
asks nicely.

Here the model receives indexed records from the profile core and
returns a *plan*: which positions, in what order, which bullets, and
the rewritten text of each — every one citing the `source_id` it came
from. karani renders the markdown itself. Three things follow:

- **Provenance.** Every line traces to a record. `trace()` renders the
  journey: source bullet -> output bullet.
- **No invented history.** A plan citing an id that does not exist is
  dropped, so the model cannot conjure a job.
- **Numbers survive by construction.** If a rewrite loses a figure the
  source bullet had, the original is kept. "Cut p99 from 1.4s to
  180ms" cannot quietly become "improved latency significantly".
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from karani.profile.model import Profile

log = logging.getLogger(__name__)

_NUMBER = re.compile(r"\d[\d,.]*\s*(?:%|[kKmMbB]\b|x\b|ms\b|s\b)?")


class BulletEdit(BaseModel):
    source_id: str
    text: str


class PositionPlan(BaseModel):
    source_id: str
    bullets: list[BulletEdit] = Field(default_factory=list)


class TailorPlan(BaseModel):
    headline: str = ""
    summary: str = ""
    positions: list[PositionPlan] = Field(default_factory=list)
    changes_summary: str = ""


def index_profile(profile: Profile) -> dict[str, object]:
    """Stable ids the model cites back: p0, p0b1, p1, ..."""
    index: dict[str, object] = {}
    for pi, position in enumerate(profile.positions):
        index[f"p{pi}"] = position
        for bi, bullet in enumerate(position.bullets):
            index[f"p{pi}b{bi}"] = bullet
    return index


def render_records(profile: Profile) -> str:
    """The indexed view the model selects from."""
    lines: list[str] = []
    for pi, position in enumerate(profile.positions):
        span = " - ".join(x for x in (position.started,
                                      position.ended or "present") if x)
        lines.append(f"[p{pi}] {position.title or 'role'} at "
                     f"{position.company}" + (f" ({span})" if span else ""))
        for bi, bullet in enumerate(position.bullets):
            lines.append(f"    [p{pi}b{bi}] {bullet}")
    return "\n".join(lines)


def _figures(text: str) -> set[str]:
    return {m.group(0).strip().rstrip(".").lower()
            for m in _NUMBER.finditer(text or "")}


def apply_plan(profile: Profile, plan: TailorPlan) -> tuple[str, list[dict]]:
    """Render the tailored resume and the provenance trail.

    Returns (markdown, trace). Unknown ids are dropped; rewrites that
    lose a source figure fall back to the original text.
    """
    index = index_profile(profile)
    trace: list[dict] = []
    out: list[str] = []

    if profile.name:
        out.append(f"# {profile.name}")
    for line in profile.contacts:
        out.append(line)
    if plan.headline:
        out.append(f"\n**{plan.headline}**")
    if plan.summary:
        out.append(f"\n## Summary\n\n{plan.summary}")

    out.append("\n## Experience")
    for position_plan in plan.positions:
        position = index.get(position_plan.source_id)
        if not hasattr(position, "company"):
            log.warning("tailor plan cites unknown position %s — dropped",
                        position_plan.source_id)
            trace.append({"source_id": position_plan.source_id,
                          "status": "dropped: unknown id"})
            continue
        span = " - ".join(x for x in (position.started,
                                      position.ended or "present") if x)
        heading = f"\n### {position.company} — {position.title}".rstrip(" —")
        out.append(heading + (f"\n*{span}*" if span else ""))

        for edit in position_plan.bullets:
            source = index.get(edit.source_id)
            if not isinstance(source, str):
                log.warning("tailor plan cites unknown bullet %s — dropped",
                            edit.source_id)
                trace.append({"source_id": edit.source_id,
                              "status": "dropped: unknown id"})
                continue
            text, status = edit.text.strip(), "rewritten"
            lost = _figures(source) - _figures(text)
            if lost:
                # The numbers are the evidence; a rewrite that drops
                # them is weaker than the original, every time.
                log.info("bullet %s lost figures %s — keeping the original",
                         edit.source_id, sorted(lost))
                text, status = source, f"kept original (lost {sorted(lost)})"
            if not text:
                continue
            out.append(f"- {text}")
            trace.append({"source_id": edit.source_id, "status": status,
                          "from": source, "to": text,
                          "position": position.company})

    if profile.education:
        out.append("\n## Education")
        for edu in profile.education:
            label = " — ".join(x for x in (edu.school, edu.degree) if x)
            out.append(f"\n### {label or edu.degree}")
    if profile.skill_groups:
        out.append("\n## Skills\n")
        out += [f"- {g}" for g in profile.skill_groups]
    elif profile.skills:
        out.append("\n## Skills\n\n" + ", ".join(profile.skills[:60]))

    return "\n".join(out).strip() + "\n", trace


def trace_report(trace: list[dict]) -> str:
    """Human-readable data journey, for the pack and for debugging."""
    lines = ["# Tailoring trace", "",
             "Every output bullet and the record it came from.", ""]
    for entry in trace:
        if entry.get("status", "").startswith("dropped"):
            lines.append(f"- [{entry['source_id']}] {entry['status']}")
            continue
        lines.append(f"- [{entry['source_id']}] {entry['position']} — "
                     f"{entry['status']}")
        if entry["from"] != entry["to"]:
            lines.append(f"    from: {entry['from']}")
            lines.append(f"    to:   {entry['to']}")
    return "\n".join(lines) + "\n"
