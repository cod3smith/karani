"""STAR stories must be mined, never invented.

Same discipline as the resume tailorer (drafting/resume_plan.py): the
model returns records citing where each claim came from, and karani
verifies the citation against the real material before anything
reaches the pack. A story with no surviving provenance is dropped —
an interview answer the candidate cannot back up is worse than no
answer at all.
"""
from __future__ import annotations

import json

import pytest

from karani.coach.stories import (STORY_PROMPT_VERSION, StarStory,
                                  mine_stories, render_stories,
                                  story_trace_report, verify_stories)
from karani.profile.model import Position, Profile

PROFILE = Profile(
    name="Kelyn Njeri",
    positions=[
        Position(company="DataQRL", title="Staff Engineer",
                 started="2022", ended="2025",
                 bullets=["Cut p99 ingest latency from 1.4s to 180ms "
                          "across 7 pipelines",
                          "Led a team of 4 through a Kafka migration"]),
    ],
)

TRANSCRIPT = (
    "The hardest part of the DataQRL migration was that nobody owned "
    "the schema registry, so every team shipped incompatible Avro."
)


def _story(**kw) -> StarStory:
    base = dict(title="Latency work", situation="s", task="t",
                action="a", result="r")
    return StarStory(**{**base, **kw})


def test_a_story_citing_a_real_bullet_survives():
    kept, trace = verify_stories([_story(source_ids=["p0b0"])],
                                 profile=PROFILE, transcript=TRANSCRIPT)
    assert len(kept) == 1
    assert kept[0].source_ids == ["p0b0"]
    assert trace[0]["status"] == "verified"


def test_an_unknown_id_is_dropped_but_the_story_survives_on_the_rest():
    kept, trace = verify_stories(
        [_story(source_ids=["p0b0", "p9b9"])],
        profile=PROFILE, transcript=TRANSCRIPT)
    assert kept[0].source_ids == ["p0b0"]
    assert any("p9b9" in entry.get("dropped_ids", []) for entry in trace)


def test_a_story_with_no_real_provenance_is_dropped_entirely():
    """The failure this exists to prevent: a plausible, fabricated
    achievement the candidate then has to defend out loud."""
    kept, trace = verify_stories([_story(title="Scaled to 10M QPS",
                                         source_ids=["p4b2"])],
                                 profile=PROFILE, transcript=TRANSCRIPT)
    assert kept == []
    assert trace[0]["status"].startswith("dropped")


def test_a_transcript_quote_must_be_verbatim():
    real = _story(title="Schema ownership",
                  transcript_quote="nobody owned the SCHEMA registry")
    invented = _story(title="Invented",
                      transcript_quote="we rewrote the whole thing in Rust")
    kept, _ = verify_stories([real, invented], profile=PROFILE,
                             transcript=TRANSCRIPT)
    assert [s.title for s in kept] == ["Schema ownership"]


def test_rendered_stories_carry_their_provenance():
    kept, trace = verify_stories([_story(source_ids=["p0b0"])],
                                 profile=PROFILE, transcript=TRANSCRIPT)
    text = render_stories(kept, PROFILE)
    assert "DataQRL" in text
    assert "1.4s to 180ms" in text          # the source bullet is shown
    assert "p0b0" in story_trace_report(trace)


# --- the mining call itself (fake client, no network) ---

STORY_JSON = json.dumps({"stories": [
    {"title": "Cutting ingest latency",
     "competencies": ["performance", "ownership"],
     "situation": "Seven pipelines missing SLA",
     "task": "Own p99", "action": "Rewrote the batcher",
     "result": "1.4s to 180ms", "source_ids": ["p0b0"],
     "transcript_quote": "nobody owned the schema registry"},
    {"title": "Fabricated", "situation": "x", "task": "x", "action": "x",
     "result": "x", "source_ids": ["p7b7"]},
]})


class FakeLLM:
    model_name = "fake"

    def __init__(self, response: str):
        self.response = response
        self.system = ""
        self.user = ""

    async def complete(self, system: str, user: str) -> str:
        self.system, self.user = system, user
        return self.response


JOB = {"id": 5, "company_display": "GitLab", "title": "Staff Engineer",
       "description_text": "Distributed systems, Kafka, latency work."}


@pytest.mark.asyncio
async def test_mining_verifies_before_returning():
    client = FakeLLM(STORY_JSON)
    stories, trace = await mine_stories(
        client, job_row=JOB, profile=PROFILE, transcript=TRANSCRIPT,
        resume="# Kelyn", competencies=["latency"])
    assert [s.title for s in stories] == ["Cutting ingest latency"]
    assert any(e["status"].startswith("dropped") for e in trace)
    # The indexed records reached the model.
    assert "p0b0" in client.system
    assert STORY_PROMPT_VERSION


@pytest.mark.asyncio
async def test_malformed_output_yields_no_stories_not_an_exception():
    stories, trace = await mine_stories(
        FakeLLM("sorry, I cannot"), job_row=JOB, profile=PROFILE,
        transcript=TRANSCRIPT, resume="# Kelyn")
    assert stories == []
    assert trace == []
