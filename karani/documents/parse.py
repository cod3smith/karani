"""Markdown resume/letter -> a small semantic model.

The renderers need structure, not prose: a header block, then sections
of paragraphs and bullets. Deliberately tolerant — tailored resumes are
model output, so anything unparseable degrades to a paragraph rather
than raising.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def strip_markup(text: str) -> str:
    """Plain text for the .docx path — ATS parsers read characters, and
    stray asterisks end up in the parsed field value."""
    text = _LINK.sub(r"\1", text)
    return _BOLD.sub(r"\1", text).replace("*", "").strip()


@dataclass
class Block:
    kind: str           # "para" | "bullet" | "subhead"
    text: str           # raw markdown — renderers decide how to treat it


@dataclass
class Section:
    title: str
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Document:
    name: str = ""
    contact: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)


def parse_markdown(md: str) -> Document:
    doc = Document()
    current: Section | None = None
    preamble: list[str] = []

    for raw in (md or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue

        heading = _HEADING.match(line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2).strip()
            if level == 1 and not doc.name:
                doc.name = strip_markup(title)
                continue
            if level <= 2:
                current = Section(title=strip_markup(title))
                doc.sections.append(current)
                continue
            # h3+ are entries WITHIN a section (a role, a project) — not
            # new sections, or every job title becomes a resume heading.
            block = Block("subhead", title)
            if current is None:
                current = Section(title="")
                doc.sections.append(current)
            current.blocks.append(block)
            continue

        bullet = _BULLET.match(line)
        block = Block("bullet", bullet.group(1).strip()) if bullet \
            else Block("para", line.strip())

        if current is None:
            # Lines above the first section: contact details, usually.
            preamble.append(block.text)
            continue
        current.blocks.append(block)

    # Raw: the PDF renders **bold** properly, the .docx strips it.
    doc.contact = preamble[:3]
    return doc
