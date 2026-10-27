"""The submission console: one file, no server, works on a phone."""
from __future__ import annotations

import re

from karani.submission.answers import Answer, AnswerSheet
from karani.submission.console import render_console


def _sheet(**kw) -> AnswerSheet:
    base = dict(
        job_id=12, company="Acme", title="Staff Backend Engineer",
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
        ats="greenhouse", introspected=True,
        legal_note="I work remotely from Kenya.",
        answers=(
            Answer(key="first_name", label="First name", value="Kelyn",
                   source="profile", required=True),
            Answer(key="q_auth", label="Do you require sponsorship?",
                   kind="select", value="No", source="legal", required=True),
            Answer(key="q_why", label="Why Acme?", kind="textarea",
                   value="Because the data platform is the product.",
                   source="pack"),
            Answer(key="eeo", label="Gender", source="voluntary"),
            Answer(key="q_k8s", label="Kubernetes experience?",
                   kind="textarea", required=True, needs_input=True),
        ),
        documents={"Kelyn_Acme_Resume.docx": "https://minio/r.docx?sig=1"},
    )
    base.update(kw)
    return AnswerSheet(**base)


def test_the_console_is_one_self_contained_file():
    html = render_console([_sheet()])
    assert html.lstrip().startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    assert 'name="viewport"' in html            # it has to work on a phone
    assert "<script src=" not in html           # no external JS
    assert "<link" not in html                  # no external CSS or fonts
    assert "@media" in html                     # responsive, not just narrow


def test_every_answer_is_present_with_its_provenance():
    html = render_console([_sheet()])
    assert "Kelyn" in html
    assert "Because the data platform is the product." in html
    assert "legal" in html and "profile" in html
    assert "Voluntary" in html or "voluntary" in html
    assert html.count("data-copy") >= 3          # a copy button per answer


def test_apply_and_document_links_are_safe_and_present():
    html = render_console([_sheet()])
    assert 'href="https://boards.greenhouse.io/acme/jobs/1"' in html
    assert 'rel="noopener' in html
    assert "https://minio/r.docx?sig=1" in html
    assert "Kelyn_Acme_Resume.docx" in html


def test_untrusted_text_is_escaped():
    sheet = _sheet(company='Acme <script>alert("x")</script> & Co',
                   answers=(Answer(key="q", label="Tell us <b>why</b>",
                                   value="a < b & c </textarea>",
                                   source="pack"),))
    html = render_console([sheet])
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    # A value containing a closing tag must not end its own textarea.
    assert "a &lt; b &amp; c &lt;/textarea&gt;" in html
    assert "<b>why</b>" not in html


def test_only_an_unresolved_attestation_earns_the_badge():
    html = render_console([_sheet(legal_review=True,
                                  legal_note="Confirm the country first.")])
    assert "check work authorization" in html
    assert "Confirm the country first." in html
    # A merely unanswered field is counted, not badged as a legal risk.
    assert "check work authorization" not in render_console([_sheet()])


def test_a_fallback_question_set_says_so():
    html = render_console([_sheet(
        introspected=False,
        question_note="This ATS does not publish its question set")])
    assert "does not publish its question set" in html


def test_marking_submitted_produces_a_write_back_command():
    html = render_console([_sheet(job_id=12), _sheet(job_id=34)])
    assert 'data-job="12"' in html and 'data-job="34"' in html
    assert "karani submitted" in html
    # The console prepares; a human clicks. Nothing in it posts anywhere.
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html


def test_an_empty_queue_renders_an_honest_page():
    html = render_console([])
    assert "<!doctype html>" in html
    assert re.search(r"nothing|empty|no packs", html, re.I)
