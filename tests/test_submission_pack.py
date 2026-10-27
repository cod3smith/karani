"""Reading a drafted pack back off disk."""
from __future__ import annotations

from karani.drafting.models import ApplicationAnswer, DraftPackage
from karani.drafting.writers import render_markdown
from karani.submission.pack import parse_pack_markdown

JOB = {"id": 7, "company_display": "GitLab", "title": "Staff Engineer",
       "apply_url": "https://example.com/apply/7"}


def test_a_rendered_pack_round_trips_back_into_its_parts():
    pkg = DraftPackage(
        cover_letter="Dear GitLab,\n\nI built the thing.\n\nKelyn",
        positioning_summary="Data-platform engineer who ships.",
        subject_line="Staff Engineer — Kelyn Njeri",
        application_answers=[
            ApplicationAnswer(question="Why do you want to work here?",
                              answer="Because the runner is remote-first.",
                              word_count=6),
            ApplicationAnswer(question="Describe a hard system you built",
                              answer="A causal-inference pipeline."),
        ],
        job_id=7, model="m", prompt_version="draft-v1",
    )
    pack = parse_pack_markdown(render_markdown(pkg, JOB))

    assert pack.cover_letter.startswith("Dear GitLab,")
    assert "Kelyn" in pack.cover_letter
    assert pack.positioning == "Data-platform engineer who ships."
    assert pack.subject_line == "Staff Engineer — Kelyn Njeri"
    assert pack.answers["Why do you want to work here?"] == \
        "Because the runner is remote-first."
    assert "causal-inference" in pack.answers["Describe a hard system you built"]
    # The word-count footers are the writer's, not content.
    assert "words_" not in pack.cover_letter


def test_an_empty_or_unparseable_pack_is_empty_not_an_error():
    assert parse_pack_markdown("").answers == {}
    assert parse_pack_markdown("just some prose").cover_letter == ""
