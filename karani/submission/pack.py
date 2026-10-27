"""The drafted pack, read back off disk.

The pack is written as markdown (drafting/writers.py) and that file is
the only durable form of the prose — the DraftPackage itself is not
persisted. The answer sheet needs the parts, not the document, so this
reverses the writer: headings back into fields. Tolerant by design, a
pack the user hand-edited is still a pack.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_WORD_COUNT_FOOTER = re.compile(r"^_\d+\s+words_$")


@dataclass
class PackContent:
    cover_letter: str = ""
    positioning: str = ""
    subject_line: str = ""
    answers: dict[str, str] = field(default_factory=dict)
    bullets: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.cover_letter or self.answers or self.positioning)


def _clean(lines: list[str]) -> str:
    kept = [ln for ln in lines if not _WORD_COUNT_FOOTER.match(ln.strip())]
    return "\n".join(kept).strip()


def parse_pack_markdown(text: str) -> PackContent:
    pack = PackContent()
    section = ""
    question = ""
    buffer: list[str] = []

    def flush() -> None:
        body = _clean(buffer)
        if not body:
            return
        if section == "cover letter":
            pack.cover_letter = body
        elif section == "positioning":
            pack.positioning = body
        elif section == "suggested subject line":
            pack.subject_line = body.lstrip("> ").strip()
        elif section == "application question answers" and question:
            pack.answers[question] = body
        elif section == "tailored resume bullets":
            pack.bullets = [
                ln.lstrip("- ").strip() for ln in body.splitlines()
                if ln.strip().startswith("-")
            ]

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            flush()
            buffer, question = [], ""
            section = line[3:].strip().lower()
            continue
        if line.startswith("### ") and section == "application question answers":
            flush()
            buffer = []
            question = line[4:].strip()
            continue
        buffer.append(line)
    flush()
    return pack


def load_pack(path: str | Path | None) -> PackContent:
    """Best-effort: a missing or unreadable draft is an empty pack, not
    a failed console row."""
    if not path:
        return PackContent()
    try:
        return parse_pack_markdown(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return PackContent()


__all__ = ["PackContent", "load_pack", "parse_pack_markdown"]
