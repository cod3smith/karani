"""Digest renderers: text / markdown / html."""
from __future__ import annotations

from ingestion.digest import render, render_html, render_markdown, render_text


def _rows():
    return [
        {"id": 1, "company_display": "GitLab", "title": "Staff Backend Engineer",
         "apply_url": "https://x/1", "location_raw": "Remote",
         "role_category": "software_engineering",
         "comp_min_usd": 200000, "comp_max_usd": 260000,
         "verdict": "qualified", "fit_score": 91,
         "qualification": {
             "fit_score": 91, "verdict": "qualified",
             "why_apply": "Direct match.",
             "recommended_positioning": "Lead with backend + payments.",
             "strengths": [{"claim": "Python + Go",
                            "evidence_from_resume": "8y consultancy"}],
             "gaps": [{"requirement": "Rust", "mitigation": "Reframe Go",
                        "severity": "medium"}],
             "red_flags": [], "evidence_gathered": [],
         }},
    ]


def test_text_renderer_non_empty():
    out = render(_rows(), "text")
    assert "GitLab" in out
    assert "QUALIFIED" in out
    assert "91" in out


def test_markdown_renderer_has_sections():
    out = render(_rows(), "md")
    assert "# Job digest" in out
    assert "**Why apply:**" in out
    assert "**Strengths:**" in out
    assert "**Gaps:**" in out
    assert "https://x/1" in out


def test_html_renderer_produces_valid_shape():
    out = render(_rows(), "html")
    assert out.startswith("<!doctype html>")
    assert out.strip().endswith("</html>")
    assert "GitLab" in out
    assert 'class="badge qualified"' in out
    assert 'python -m ingestion.cli verdict 1 apply' in out


def test_html_renderer_empty_state():
    out = render_html([])
    assert 'class="empty"' in out


def test_html_escapes_user_content():
    rows = _rows()
    rows[0]["title"] = "<script>alert('xss')</script>"
    out = render_html(rows)
    assert "<script>alert" not in out
    assert "&lt;script&gt;" in out
