# 0013 · LangGraph for background orchestration — and nowhere else

**Status:** accepted

## Context

The hourly hunt was a make-chained sequence of CLI invocations with `-`
prefixes swallowing errors: no per-stage retry, no structured run state,
failures visible only in log files. Kelyn wants the system prod-like and
asked for LangGraph explicitly.

Blunt assessment first, because it shaped the scope: karani's hunt is a
LINEAR, IDEMPOTENT pipeline whose durable state machine already lives in
Postgres. LangGraph does not make qualification smarter, and re-platforming
the thin adapters (CLI/MCP/Slack) onto it would be architecture theater.
What LangGraph legitimately buys at the orchestration layer: explicit
graph structure, per-node retry and error containment, one structured
run-state object instead of four exit codes, failure alerting, and a
growth path to branching flows (deep-research routing, human-interrupt
nodes) without re-platforming later.

## Decision

`orchestration/` — a LangGraph `StateGraph` for the hunt pass:

    START -> ingest -> qualify -> autopilot -> notion -> report -> END

Scope rules:

1. **Nodes wrap, never re-implement.** Each node calls the same runner
   the CLI and MCP server call. The graph is a fourth thin adapter.
2. **Failures are contained per node** (one retry, then the error lands
   in state); later nodes still run, and `report` alerts Slack when
   anything failed. The best-effort semantics of the make chain are
   preserved, but now observable and alertable.
3. **No checkpointer, deliberately.** Every node is idempotent against
   the system of record (upsert dedup, resume-hash qualification,
   drafted_at budget), so re-running a crashed pass from START is safe
   and loses nothing — a checkpointer would add a psycopg dependency and
   schema for zero recovery value. Revisit the moment the graph gains
   branching or human-interrupt nodes, where mid-run state is real.
4. **Optional dependency** (`uv sync --extra orchestrator`). `make
   hourly` runs the graph and falls back to the legacy chain
   (`hourly-legacy`) when langgraph isn't installed — the scheduler
   never breaks over a missing extra.
5. **LangGraph stays out of** the pre-filter (deterministic, rule 4.9),
   the qualification agent loop (a working ~100-line tool loop; porting
   it is churn without capability), and the interface adapters.

## Consequences

- **Positive:** one structured pass with retries, contained failures,
  Slack alerting, and a mermaid-renderable topology
  (`python -m orchestration --show`).
- **Positive:** the future branching work (route top candidates through
  deep research before drafting; pause a pass on a human interrupt) now
  has a home that supports it natively.
- **Negative:** a heavyweight dependency tree (langchain-core et al.)
  for what is today a linear chain. Contained: optional extra, one
  package, fallback preserved.
- **Negative:** two orchestration paths (graph + legacy chain) until the
  legacy one is retired after a few weeks of scheduled graph runs.
