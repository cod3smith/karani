"""Lifting a built-in exclusion (ADR 0021).

The built-in title exclusions encode a default software-engineering
hunt, and config could only ever add to them. A candidate whose own
background includes an excluded domain — Kelyn's NeoRx work is
biomedical ML with do-calculus and RL — had no way to say so, and the
pre-filter dropped those roles before any model saw them.
"""
from __future__ import annotations

import pytest

from karani.config import reload_config


@pytest.fixture(autouse=True)
def _restore():
    yield
    reload_config()


def _write(tmp_path, body):
    f = tmp_path / "karani.toml"
    f.write_text(body)
    return f


def test_allow_titles_lifts_a_builtin_exclusion(tmp_path):
    from karani.ingestion.config import settings
    before = settings.excluded_title_terms
    try:
        reload_config(_write(tmp_path, """
version = 1
[profile]
allow_titles = ["computational biology", "bioinformatics", "genomics"]
"""))
        terms = settings.excluded_title_terms
        assert "computational biology" not in terms
        assert "bioinformatics" not in terms
        # Everything else still excluded.
        assert "recruiter" in terms and "sales" in terms
    finally:
        settings.excluded_title_terms = before


def test_allow_and_exclude_compose(tmp_path):
    from karani.ingestion.config import settings
    before = settings.excluded_title_terms
    try:
        reload_config(_write(tmp_path, """
version = 1
[profile]
allow_titles = ["genomics"]
exclude_titles = ["blockchain wizard"]
"""))
        assert "genomics" not in settings.excluded_title_terms
        assert "blockchain wizard" in settings.excluded_title_terms
    finally:
        settings.excluded_title_terms = before


def test_allowed_domain_role_survives_the_prefilter(tmp_path):
    """The point of the feature: the role reaches the LLM tier."""
    from karani.ingestion.config import settings
    from karani.ingestion.filters import pre_filter
    from karani.ingestion.models import Job, RemoteStatus, Source
    from karani.ingestion.profile import DEFAULT_PROFILE

    job = Job(
        source=Source.GREENHOUSE, source_id="1", company="insitro",
        company_display="Insitro",
        title="Research Engineer, Computational Biology",
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=("We hire globally and pay SF bands. Python, "
                          "causal inference and machine learning. "
                          "Salary: $200,000 - $260,000."),
        apply_url="https://example.com/1",
    ).finalize()

    before = settings.excluded_title_terms
    try:
        reload_config(_write(tmp_path, "version = 1\n"))
        blocked = pre_filter(job, DEFAULT_PROFILE)
        assert not blocked.pass_hard_filters

        reload_config(_write(tmp_path, """
version = 1
[profile]
allow_titles = ["computational biology", "computational biologist"]
"""))
        allowed = pre_filter(job, DEFAULT_PROFILE)
        assert allowed.pass_hard_filters, allowed.reasons_failed
    finally:
        settings.excluded_title_terms = before


def test_persona_drops_the_exclusion_line_when_nothing_is_excluded(tmp_path):
    """With no excluded domain, the prompt must not say roles in '' are
    OUT — an empty exclusion sentence confuses the model."""
    from karani.qualification.prompts import persona
    reload_config(_write(tmp_path, """
version = 1
[positioning]
excluded_domains = ""
"""))
    text = persona()
    assert "are OUT" not in text
