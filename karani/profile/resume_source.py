"""The resume as a profile source.

Reuses the document parser (karani/documents/parse.py) rather than a
second markdown reader: one place to fix when a heading style changes.
The resume is the primary source — the candidate curates it, so its
values win when sources disagree.
"""
from __future__ import annotations

import re

from karani.documents.parse import parse_markdown, strip_markup

from .model import Education, Position, Profile, Project

# "DataQRL — Co-Founder, Chief Engineer" / "Company - Title"
_ROLE = re.compile(r"^(?P<company>.+?)\s+[—–-]\s+(?P<title>.+)$")


def _section(doc, *names: str):
    for section in doc.sections:
        if section.title.strip().lower() in {n.lower() for n in names}:
            return section
    return None


def parse_resume(markdown: str) -> Profile:
    doc = parse_markdown(markdown or "")
    positions: list[Position] = []
    experience = _section(doc, "Experience", "Work Experience",
                          "Professional Experience")
    if experience:
        current: Position | None = None
        bullets: list[str] = []
        for block in experience.blocks:
            if block.kind == "subhead":
                if current:
                    positions.append(current.model_copy(
                        update={"bullets": tuple(bullets)}))
                    bullets = []
                text = strip_markup(block.text)
                m = _ROLE.match(text)
                current = Position(
                    company=(m.group("company").strip() if m else text),
                    title=(m.group("title").strip() if m else ""))
            elif block.kind == "bullet" and current:
                bullets.append(strip_markup(block.text))
        if current:
            positions.append(current.model_copy(
                update={"bullets": tuple(bullets)}))

    education: list[Education] = []
    edu = _section(doc, "Education")
    if edu:
        for block in edu.blocks:
            if block.kind == "subhead":
                education.append(Education(degree=strip_markup(block.text)))

    # Skills and projects were dropped entirely by the first version,
    # which cost the records-tailored resume its Skills section.
    skills: list[str] = []
    skills_section = _section(doc, "Skills", "Technical Skills",
                              "Core Skills")
    if skills_section:
        for block in skills_section.blocks:
            text = strip_markup(block.text)
            # "Languages: Python, Go" -> drop the label, keep the list.
            if ":" in text:
                text = text.split(":", 1)[1]
            skills += [s.strip() for s in re.split(r"[,;|]", text)
                       if s.strip()]

    projects: list[Project] = []
    projects_section = _section(doc, "Selected Projects", "Projects",
                                "Notable Projects")
    if projects_section:
        current_project: Project | None = None
        for block in projects_section.blocks:
            text = strip_markup(block.text)
            if block.kind == "subhead":
                if current_project:
                    projects.append(current_project)
                current_project = Project(name=text)
            elif current_project and text:
                current_project = current_project.model_copy(update={
                    "description": (current_project.description + " "
                                    + text).strip()})
        if current_project:
            projects.append(current_project)

    summary = ""
    summary_section = _section(doc, "Summary", "Profile", "About")
    if summary_section:
        summary = " ".join(strip_markup(b.text)
                           for b in summary_section.blocks
                           if b.kind == "para")

    return Profile(name=doc.name, summary=summary,
                   contacts=tuple(strip_markup(c) for c in doc.contact),
                   positions=tuple(positions), education=tuple(education),
                   projects=tuple(projects),
                   skills=tuple(dict.fromkeys(skills)),
                   sources=("resume",))
