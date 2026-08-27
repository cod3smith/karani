"""Discovery: slug candidate generation."""
from __future__ import annotations

from ingestion.discovery import slug_candidates


def test_slug_candidates_multiword():
    got = slug_candidates("Hugging Face Inc.")
    assert "huggingface" in got
    assert "hugging-face" in got
    assert "hugging" in got


def test_slug_candidates_singleword():
    got = slug_candidates("GitLab")
    assert got == ["gitlab"]


def test_slug_candidates_empty():
    assert slug_candidates("") == []
    assert slug_candidates("   ") == []


def test_slug_candidates_strips_corporate_suffix():
    got = slug_candidates("Acme Corporation")
    assert "acme" in got
    assert "acmecorporation" not in got  # Corporation stripped
