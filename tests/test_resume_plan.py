"""Tailoring as selection over records (career-runner Phase 1).

The prose tailorer took a resume and returned a resume: nothing
downstream could say where a bullet came from, and the facts survived
only because the prompt asked. A plan cites the record behind every
line, so the journey is traceable and two whole classes of failure
become impossible rather than discouraged.
"""
from __future__ import annotations

from karani.drafting.resume_plan import (
    BulletEdit, PositionPlan, TailorPlan, apply_plan, index_profile,
    render_records, trace_report,
)
from karani.profile.model import Education, Position, Profile

PROFILE = Profile(
    name="Kelyn Paul Njeri",
    contacts=("Nairobi, Kenya · kelyn.njeri@gmail.com",),
    positions=(
        Position(company="DataQRL", title="Chief Engineer", started="2023",
                 bullets=("Built a 7-module platform, 10M+ records daily.",
                          "Cut p99 from 1.4s to 180ms.")),
        Position(company="PowWater", title="Senior Engineer",
                 started="2021", ended="2023",
                 bullets=("Shipped the dispatch service.",)),
    ),
    education=(Education(school="JKUAT", degree="BSc Computer Science"),),
    skills=("Python", "Go"),
)


def test_records_are_indexed_with_stable_ids():
    index = index_profile(PROFILE)
    assert index["p0"].company == "DataQRL"
    assert index["p0b1"] == "Cut p99 from 1.4s to 180ms."
    rendered = render_records(PROFILE)
    assert "[p0b0]" in rendered and "[p1]" in rendered


def test_plan_reorders_and_renders_from_records():
    plan = TailorPlan(
        headline="Staff engineer, data platforms",
        summary="Eight years on distributed systems.",
        positions=[
            PositionPlan(source_id="p1", bullets=[
                BulletEdit(source_id="p1b0",
                           text="Shipped the dispatch service end to end.")]),
            PositionPlan(source_id="p0", bullets=[
                BulletEdit(source_id="p0b0",
                           text="Built a 7-module platform, 10M+ records daily.")]),
        ])
    md, trace = apply_plan(PROFILE, plan)
    # PowWater first: the plan's order is the document's order.
    assert md.index("PowWater") < md.index("DataQRL")
    assert "Kelyn Paul Njeri" in md and "JKUAT" in md
    assert len(trace) == 2
    assert trace[0]["source_id"] == "p1b0"


def test_a_rewrite_that_loses_numbers_keeps_the_original():
    """The figures are the evidence. "Cut p99 from 1.4s to 180ms" must
    not become "improved latency significantly"."""
    plan = TailorPlan(positions=[PositionPlan(source_id="p0", bullets=[
        BulletEdit(source_id="p0b1", text="Improved latency significantly.")])])
    md, trace = apply_plan(PROFILE, plan)
    assert "1.4s to 180ms" in md
    assert "significantly" not in md
    assert trace[0]["status"].startswith("kept original")


def test_a_rewrite_that_keeps_the_numbers_is_used():
    plan = TailorPlan(positions=[PositionPlan(source_id="p0", bullets=[
        BulletEdit(source_id="p0b1",
                   text="Reworked ingestion: p99 1.4s -> 180ms.")])])
    md, trace = apply_plan(PROFILE, plan)
    assert "Reworked ingestion" in md
    assert trace[0]["status"] == "rewritten"


def test_an_invented_record_is_dropped():
    """A plan citing an id that does not exist cannot conjure a job."""
    plan = TailorPlan(positions=[
        PositionPlan(source_id="p9", bullets=[
            BulletEdit(source_id="p9b0", text="Led the Mars programme.")]),
        PositionPlan(source_id="p0", bullets=[
            BulletEdit(source_id="p0b0",
                       text="Built a 7-module platform, 10M+ records daily."),
            BulletEdit(source_id="p0b7", text="Invented a bullet.")]),
    ])
    md, trace = apply_plan(PROFILE, plan)
    assert "Mars" not in md and "Invented a bullet" not in md
    assert any(t["status"].startswith("dropped") for t in trace)


def test_trace_reads_as_a_data_journey():
    plan = TailorPlan(positions=[PositionPlan(source_id="p0", bullets=[
        BulletEdit(source_id="p0b0",
                   text="Shipped a 7-module platform at 10M+ records daily.")])])
    _, trace = apply_plan(PROFILE, plan)
    report = trace_report(trace)
    assert "p0b0" in report and "from:" in report and "to:" in report


# --- the tailorer chooses its path ---

class PlanLLM:
    model_name = "fake"

    def __init__(self, payload):
        self.payload, self.prompts = payload, []

    async def complete(self, system, user):
        self.prompts.append((system, user))
        return self.payload


import pytest  # noqa: E402


@pytest.mark.asyncio
async def test_tailorer_uses_records_when_the_profile_has_positions(tmp_path):
    from karani.drafting.resume_tailor import tailor_resume
    client = PlanLLM('''{"headline": "Staff engineer",
      "summary": "Data platforms.",
      "positions": [{"source_id": "p0", "bullets": [
        {"source_id": "p0b0",
         "text": "Built a 7-module platform at 10M+ records daily."}]}],
      "changes_summary": "- led with the platform"}''')
    result, path = await tailor_resume(
        client, resume="# K", job_row={"id": 1, "company_display": "GitLab",
                                       "title": "Staff", "description_text": "Python"},
        output_path=tmp_path / "out.md", profile=PROFILE)

    assert result.strategy == "records"
    assert result.trace and result.trace[0]["source_id"] == "p0b0"
    assert "career_records" in client.prompts[0][1]
    assert "10M+ records daily" in path.read_text()


@pytest.mark.asyncio
async def test_tailorer_falls_back_to_prose_without_records(tmp_path):
    from karani.drafting.resume_tailor import tailor_resume
    from karani.profile.model import Profile as P
    client = PlanLLM('{"resume_markdown": "# K\\n\\nprose", '
                     '"changes_summary": "-"}')
    result, _ = await tailor_resume(
        client, resume="# K", job_row={"id": 1, "title": "Staff",
                                       "description_text": "Python"},
        output_path=tmp_path / "out.md", profile=P())
    assert result.strategy == "prose"
    assert result.resume_markdown.strip().endswith("prose")


@pytest.mark.asyncio
async def test_a_fumbled_plan_falls_back_rather_than_failing(tmp_path):
    """A plan citing only unknown ids selects nothing; the prose path
    is better than an empty resume."""
    from karani.drafting.resume_tailor import tailor_resume
    client = PlanLLM('{"positions": [{"source_id": "p99", "bullets": []}]}')
    result, _ = await tailor_resume(
        client, resume="# K", job_row={"id": 1, "title": "Staff",
                                       "description_text": "Python"},
        output_path=tmp_path / "out.md", profile=PROFILE)
    assert result.strategy == "prose"
