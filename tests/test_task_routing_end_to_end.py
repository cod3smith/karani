"""Per-task routing must survive the call sites (ADR 0017).

`build_application_pack` routes each stage through `[llm.<task>]` —
but only when the caller passes `client=None`. Every caller (CLI,
autopilot, MCP, Slack) built one client and handed it in, which pinned
the whole pack to that single model. In production that meant the
cover letter, the humanizer and the tailored resume all ran on the
qualify tier: Haiku at 4000 max_tokens, which is not enough for a pack
and returned malformed JSON.
"""
from __future__ import annotations

import pytest

from karani.ingestion.storage import Storage


class RecordingClient:
    """Stands in for one task's model and records which task asked."""
    def __init__(self, task: str, seen: list[str]):
        self.model_name = f"model-for-{task}"
        self._task, self._seen = task, seen

    async def complete(self, system, user):
        self._seen.append(self._task)
        if self._task == "tailor":
            return '{"resume_markdown": "# K", "changes_summary": "-"}'
        if self._task == "humanize":
            return '{"cover_letter": "Eight years of Kafka.", ' \
                   '"application_answers": []}'
        return ('{"cover_letter": "Dear team", "tone_note": "", '
                '"tailored_bullets": [], "application_answers": [], '
                '"subject_line": "", "positioning_summary": ""}')


@pytest.mark.asyncio
async def test_each_pack_stage_uses_its_own_configured_model(
        tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KARANI_TAILOR_RESUME", "on")
    seen: list[str] = []

    # pipeline imports get_qualifier inside the function, so the patch
    # has to land on the package attribute it resolves at call time.
    import karani.qualification as qualification
    monkeypatch.setattr(qualification, "get_qualifier",
                        lambda provider=None, model=None, task="qualify":
                        RecordingClient(task, seen))
    import karani.drafting.pipeline as pipeline

    storage = Storage("")
    await storage.connect()
    row = {"id": 1, "company_display": "GitLab", "title": "Staff Engineer",
           "description_text": "Python and Kafka.", "location_raw": "Remote"}

    await pipeline.build_application_pack(
        None, storage, job_row=row, resume_markdown="# K")

    # Three stages, three different task routes — not one model thrice.
    assert "draft" in seen, seen
    assert len(set(seen)) > 1, f"all stages used one model: {seen}"


def test_graph_does_not_hand_the_qualify_client_to_drafting():
    """The production bug, as a rule: run_autopilot's make_qualifier
    pins every pack stage to one model, so the graph must pass its
    dedicated pack-client seam (None in production) — never
    deps.make_qualifier, which is the qualify tier."""
    import pathlib
    src = pathlib.Path("karani/orchestration/graph.py").read_text()
    lines = src.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if "run_autopilot(" in ln and "import" not in ln), None)
    assert start is not None, "run_autopilot call not found"
    args = "\n".join(lines[start:start + 14])
    assert "make_qualifier=deps.make_pack_client" in args, args
    assert "make_qualifier=deps.make_qualifier" not in args


def test_surface_callers_do_not_pin_the_pack():
    """CLI, MCP and Slack build a qualify-tier client for their own
    use; handing it to the pack would silently route the cover letter
    there too. Only an explicit --provider/--model override may."""
    import pathlib
    import re
    offenders = []
    for path in ("karani/cli.py", "karani/mcp_server/server.py",
                 "karani/slackbridge/commands.py"):
        src = pathlib.Path(path).read_text()
        for match in re.finditer(
                r"build_application_pack\(\s*\n?\s*([A-Za-z_][A-Za-z0-9_]*)",
                src):
            name = match.group(1)
            if name not in ("None", "override", "client_override"):
                offenders.append(f"{path}: passes {name!r}")
    assert not offenders, (
        "these pin every stage to one model: " + "; ".join(offenders))
