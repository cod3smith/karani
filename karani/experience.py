"""The career transcript: what the resume cannot hold.

A resume is compressed to the point of being generic — "built a
7-module data-governance platform" says nothing about the decision
that made it work, the thing that nearly failed, or why the candidate
chose that architecture. Those details are what a specific cover
letter is made of, and they only exist in the candidate's head.

So karani takes a transcript: the candidate talking through their
career, however rambling. It is loaded verbatim into the STABLE half
of the prompt (ADR 0019), which means it is cached — a long transcript
costs ~0.1x of input on every row after the first.

Treated as ground truth about the candidate's history and never
summarized at load time: compression is what produced the generic
resume in the first place.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = "data/experience.md"
# Transcripts ramble; this is generous on purpose. ~12k words.
MAX_CHARS = 80_000


def experience_path() -> Path:
    from karani.config.loader import karani_home
    override = os.getenv("KARANI_EXPERIENCE")
    if override:
        return Path(override)
    local = Path(DEFAULT_PATH)
    return local if local.exists() else karani_home() / "experience.md"


def load_experience(path: str | Path | None = None) -> str:
    """The transcript, or "" when there is none. Never raises — an
    absent transcript degrades personalization, it does not stop a
    hunt."""
    target = Path(path) if path else experience_path()
    try:
        if not target.exists():
            return ""
        text = target.read_text(encoding="utf-8").strip()
    except OSError as exc:
        log.warning("could not read experience transcript at %s: %s",
                    target, exc)
        return ""
    if len(text) > MAX_CHARS:
        log.warning("experience transcript is %d chars; using the first %d",
                    len(text), MAX_CHARS)
        text = text[:MAX_CHARS]
    return text


def experience_hash(text: str = "") -> str:
    """Changing the transcript changes the tailoring, so it belongs in
    provenance alongside the resume hash."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]
