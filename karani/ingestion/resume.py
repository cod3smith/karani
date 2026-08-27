"""ResumeProfile — the source of truth for who Kelyn is.

Kept as a markdown file at `data/resume.md` (path is configurable via
`RESUME_PATH`). We do NOT parse the markdown into structured sections here —
the LLM is much better at that. We just wrap the raw text, add a stable
content hash so downstream caches can invalidate when the resume changes,
and support optional structured overrides via a companion YAML file.

Rationale: brittle regex parsing of a resume is a maintenance sink. Let the
model handle interpretation; keep this layer thin.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_RESUME_PATH = os.getenv("RESUME_PATH", "data/resume.md")


@dataclass
class ResumeProfile:
    raw_markdown: str
    # Freeform extras a user can pin (e.g. "always mention Nairobi timezone").
    hints: list[str] = field(default_factory=list)
    # Companies to never apply to (acquired competitors, past employers, etc.)
    blocklist_companies: list[str] = field(default_factory=list)

    @property
    def hash(self) -> str:
        """SHA-256 of the raw resume. Used to invalidate cached qualifications."""
        return hashlib.sha256(self.raw_markdown.encode("utf-8")).hexdigest()

    @property
    def word_count(self) -> int:
        return len(self.raw_markdown.split())

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> "ResumeProfile":
        path = Path(path or DEFAULT_RESUME_PATH)
        if not path.exists():
            raise FileNotFoundError(
                f"Resume not found at {path}. Copy data/resume.md.example "
                f"to {path} and fill it in."
            )
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            raise ValueError(f"Resume at {path} is empty.")
        return cls(raw_markdown=raw)


def load_default_resume() -> ResumeProfile:
    return ResumeProfile.from_file()
