"""The merged profile core (career-runner Phase 1).

A resume is a rendering of a career; tailoring by rewriting that
rendering is why bullets drift and numbers have to be preserved by
instruction. The profile is the career as records, merged from the
resume, the LinkedIn export and the candidate's own site.
"""
from __future__ import annotations

import csv
import io
import zipfile

from karani.profile import load_profile, parse_resume, profile_context
from karani.profile.linkedin import parse_export
from karani.profile.model import Position, Profile
from karani.profile.portfolio import html_to_text

RESUME = """# Kelyn Paul Njeri
Nairobi, Kenya · kelyn.njeri@gmail.com

## Summary

Staff engineer, data platforms and causal ML.

## Experience

### DataQRL — Co-Founder, Chief Engineer
- Built a 7-module governance platform, 10M+ records daily.
- Cut p99 from 1.4s to 180ms.

### PowWater — Senior Software Engineer
- Shipped the dispatch service.

## Education

### BSc Computer Science
"""


def _export(tmp_path, positions, skills=("Python", "Go")):
    """A LinkedIn export ZIP, as the member downloads it."""
    path = tmp_path / "linkedin.zip"
    with zipfile.ZipFile(path, "w") as zf:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["Company Name", "Title",
                                            "Started On", "Finished On",
                                            "Description"])
        w.writeheader()
        for p in positions:
            w.writerow(p)
        zf.writestr("Positions.csv", buf.getvalue())
        sbuf = io.StringIO()
        sw = csv.DictWriter(sbuf, fieldnames=["Name"])
        sw.writeheader()
        for s in skills:
            sw.writerow({"Name": s})
        zf.writestr("Skills.csv", sbuf.getvalue())
    return path


# --- resume as a source ---

def test_resume_parses_into_positions_with_bullets():
    p = parse_resume(RESUME)
    assert p.name == "Kelyn Paul Njeri"
    assert [x.company for x in p.positions] == ["DataQRL", "PowWater"]
    assert p.positions[0].title == "Co-Founder, Chief Engineer"
    assert len(p.positions[0].bullets) == 2
    assert "10M+ records daily" in p.positions[0].bullets[0]
    assert "causal ML" in p.summary


# --- linkedin export ---

def test_linkedin_export_is_parsed_from_a_zip(tmp_path):
    path = _export(tmp_path, [
        {"Company Name": "DataQRL", "Title": "Co-Founder, Chief Engineer",
         "Started On": "2023-01", "Finished On": "",
         "Description": "Governance platform."},
    ])
    p = parse_export(path)
    assert p.positions[0].company == "DataQRL"
    assert p.positions[0].started == "2023-01"
    assert "Python" in p.skills
    assert p.sources == ("linkedin-export",)


def test_unreadable_export_yields_an_empty_profile(tmp_path):
    bad = tmp_path / "nope.zip"
    bad.write_text("not a zip")
    assert parse_export(bad).positions == ()


# --- merging ---

def test_resume_wins_and_export_fills_the_gaps(tmp_path):
    resume = parse_resume(RESUME)
    export = parse_export(_export(tmp_path, [
        {"Company Name": "DataQRL", "Title": "Co-Founder, Chief Engineer",
         "Started On": "2023-01", "Finished On": "",
         "Description": "From LinkedIn."},
        {"Company Name": "Sonolabs", "Title": "Engineer",
         "Started On": "2019-01", "Finished On": "2020-01",
         "Description": "Earlier role the resume omits."},
    ]))
    merged = resume.merged_with(export)

    same = [p for p in merged.positions if p.company == "DataQRL"][0]
    assert same.bullets, "resume bullets must survive the merge"
    assert same.started == "2023-01", "export fills the missing date"
    assert any(p.company == "Sonolabs" for p in merged.positions), \
        "a role only LinkedIn knows about should be added"
    assert set(merged.sources) == {"resume", "linkedin-export"}


def test_merge_never_overwrites_a_curated_value():
    curated = Profile(positions=(Position(company="X", title="Staff Engineer",
                                          started="2021"),))
    other = Profile(positions=(Position(company="X", title="Staff Engineer",
                                        started="1999"),))
    assert curated.merged_with(other).positions[0].started == "2021"


# --- rendering for the prompt ---

def test_context_is_empty_when_there_is_nothing_beyond_the_resume():
    assert profile_context(Profile()) == ""


def test_context_carries_positions_and_skills(tmp_path):
    merged = parse_resume(RESUME).merged_with(parse_export(_export(
        tmp_path,
        [{"Company Name": "Sonolabs", "Title": "Engineer",
          "Started On": "2019-01", "Finished On": "2020-01",
          "Description": "Kubernetes and Helm."}])))
    text = profile_context(merged, portfolio_text="I write about causal ML.")
    assert "Sonolabs" in text and "Kubernetes" in text
    assert "Python" in text
    assert "causal ML" in text
    assert "never contradict the resume" in text.lower()


def test_portfolio_html_reduces_to_text():
    html = "<html><head><style>p{}</style></head><body><h1>Kelyn</h1>" \
           "<script>x()</script><p>Causal inference work.</p></body></html>"
    text = html_to_text(html)
    assert "Causal inference work." in text
    assert "x()" not in text and "p{}" not in text


def test_load_profile_works_with_no_external_sources(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    p = load_profile(RESUME)
    assert p.positions and p.sources == ("resume",)


# --- sections the first parser dropped on the floor ---

RICH = RESUME + """
## Selected Projects

### NeoRx — causal drug-resistance pipeline
Do-calculus with RL, F1 0.474 across 7 disease validations.

## Skills

**Languages:** Python, Go, TypeScript
**Infra:** Kubernetes, Kafka, Postgres
"""


def test_skills_and_projects_are_extracted():
    """They were dropped, so the records-tailored resume lost its
    Skills section and keyword coverage fell from 0.57 to 0.33."""
    p = parse_resume(RICH)
    assert "Python" in p.skills and "Kubernetes" in p.skills
    assert any("NeoRx" in proj.name for proj in p.projects)


def test_skill_extraction_keeps_both_values_and_labels():
    """Labels were dropped as "not skills" until a real draft showed
    the cost: "Monitoring & Observability" is itself a term the JD
    matches on, and losing it cost measurable keyword coverage."""
    p = parse_resume(RICH)
    assert "Go" in p.skills                 # the values
    assert "Languages" in p.skills          # and the category label


def test_skill_group_labels_survive():
    """"Monitoring & Observability: Prometheus, Grafana" — the label is
    a keyword in its own right, and stripping it cost "observability"
    from the tailored resume."""
    p = parse_resume(RICH)
    grouped = " ".join(p.skill_groups).lower()
    assert "languages" in grouped and "infra" in grouped
    assert "python" in grouped
