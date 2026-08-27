"""The hunt graph.

    START -> ingest -> qualify -> autopilot -> notion -> report -> END

Design rules (ADR 0013):
- Every node is a thin wrapper over the SAME runner the CLI/MCP call —
  the graph orchestrates, it never re-implements.
- A node failure is contained: the error lands in state, later nodes
  still run (the hunt is best-effort per stage), and `report` alerts
  Slack when anything failed. Each node retries once before giving up.
- No checkpointer: every node is idempotent against Postgres (upsert
  dedup, resume-hash qualification, drafted_at budget), so re-running a
  crashed pass from START is always safe and loses nothing. Revisit when
  the graph gains branching or human-interrupt nodes.
- Dependency injection via `build_hunt_graph(deps)` so tests wire fakes.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

log = logging.getLogger(__name__)


def _merge_errors(left: list[str], right: list[str]) -> list[str]:
    return left + right


class HuntState(TypedDict, total=False):
    ingest: dict[str, Any]
    qualify: dict[str, Any]
    autopilot: dict[str, Any]
    notion: dict[str, Any]
    alerted: bool
    errors: Annotated[list[str], _merge_errors]


@dataclass
class HuntDeps:
    """Everything the nodes need, injectable for tests."""
    storage: Any
    make_qualifier: Callable[[], Any]
    load_resume: Callable[[], Any]
    slack_factory: Callable[[], Any] | None = None
    channel: str = ""
    qualify_limit: int = int(os.getenv("HUNT_QUALIFY_LIMIT", "50"))
    memory: Any = None
    extras: dict[str, Any] = field(default_factory=dict)


async def _with_retry(name: str, fn, attempts: int = 2) -> tuple[Any, str | None]:
    """Run a node body with one retry; returns (result, error)."""
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return await fn(), None
        except Exception as exc:
            last = exc
            log.warning("hunt node %s failed (attempt %d/%d): %s",
                        name, i, attempts, exc)
    return None, f"{name}: {last}"


def build_hunt_graph(deps: HuntDeps):
    async def ingest(state: HuntState) -> dict:
        from karani.ingestion.orchestrator import run as run_ingestion

        async def body():
            stats = await run_ingestion(deps.storage)
            return {"fetched": stats.fetched, "inserted": stats.inserted,
                    "passed_prefilter": stats.passed_prefilter,
                    "source_errors": sum(o.errors
                                         for o in stats.per_source.values())}
        result, err = await _with_retry("ingest", body)
        return ({"ingest": result} if result is not None
                else {"errors": [err]})

    async def qualify(state: HuntState) -> dict:
        from karani.qualification import qualify_pending

        async def body():
            stats = await qualify_pending(
                deps.storage, deps.make_qualifier(), deps.load_resume(),
                limit=deps.qualify_limit, memory=deps.memory,
            )
            return {"fetched": stats.fetched, "qualified": stats.qualified,
                    "maybe": stats.maybe, "skipped": stats.skipped,
                    "errors": len(stats.errors)}
        result, err = await _with_retry("qualify", body)
        return ({"qualify": result} if result is not None
                else {"errors": [err]})

    async def autopilot(state: HuntState) -> dict:
        from karani.autopilot import run_autopilot

        if not deps.slack_factory or not deps.channel:
            return {"autopilot": {"skipped": "slack not configured"}}

        async def body():
            stats = await run_autopilot(
                deps.storage, slack=deps.slack_factory(),
                channel=deps.channel,
                make_qualifier=deps.make_qualifier,
                load_resume=deps.load_resume,
            )
            return {"candidates": stats.candidates, "drafted": stats.drafted,
                    "delivered": stats.delivered,
                    "budget_left": stats.budget_left,
                    "errors": len(stats.errors)}
        result, err = await _with_retry("autopilot", body)
        return ({"autopilot": result} if result is not None
                else {"errors": [err]})

    async def notion(state: HuntState) -> dict:
        from karani.notionsync import NotionClient, sync_jobs

        database_id = os.getenv("NOTION_DATABASE_ID", "")
        if not database_id or not os.getenv("NOTION_TOKEN", ""):
            return {"notion": {"skipped": "notion not configured"}}

        async def body():
            return await sync_jobs(deps.storage, NotionClient(), database_id)
        result, err = await _with_retry("notion", body)
        return ({"notion": result} if result is not None
                else {"errors": [err]})

    async def report(state: HuntState) -> dict:
        errors = state.get("errors", [])
        summary = (
            f"hunt pass: ingest={state.get('ingest')} "
            f"qualify={state.get('qualify')} "
            f"autopilot={state.get('autopilot')} "
            f"notion={state.get('notion')} errors={len(errors)}"
        )
        log.info(summary)
        if errors and deps.slack_factory and deps.channel:
            try:
                await deps.slack_factory().post_message(
                    deps.channel,
                    "karani hunt pass hit errors:\n"
                    + "\n".join(f"- {e}" for e in errors[:5]),
                )
                return {"alerted": True}
            except Exception as exc:  # alerting must never fail the pass
                log.warning("failed to alert slack: %s", exc)
        return {"alerted": False}

    g = StateGraph(HuntState)
    g.add_node("ingest", ingest)
    g.add_node("qualify", qualify)
    g.add_node("autopilot", autopilot)
    g.add_node("notion", notion)
    g.add_node("report", report)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "qualify")
    g.add_edge("qualify", "autopilot")
    g.add_edge("autopilot", "notion")
    g.add_edge("notion", "report")
    g.add_edge("report", END)
    return g.compile()


async def run_hunt_once() -> HuntState:
    """Wire real dependencies and run one pass. The entry the scheduler hits."""
    from karani.ingestion.config import settings
    from karani.ingestion.resume import DEFAULT_RESUME_PATH, ResumeProfile
    from karani.ingestion.storage import Storage
    from karani.memory import MemoryManager
    from karani.qualification import get_qualifier

    storage = Storage(settings.database_url)
    await storage.connect()
    try:
        channel = os.getenv("SLACK_CHANNEL", "")
        slack_factory = None
        if channel and os.getenv("SLACK_BOT_TOKEN"):
            from karani.slackbridge import SlackClient
            slack_factory = SlackClient
        deps = HuntDeps(
            storage=storage,
            make_qualifier=lambda: get_qualifier(),
            load_resume=lambda: ResumeProfile.from_file(DEFAULT_RESUME_PATH),
            slack_factory=slack_factory,
            channel=channel,
            memory=MemoryManager(storage),
        )
        graph = build_hunt_graph(deps)
        return await graph.ainvoke({})
    finally:
        await storage.close()
