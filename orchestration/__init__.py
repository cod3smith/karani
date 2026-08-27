"""LangGraph background orchestration for the continuous hunt (ADR 0013).

One graph = one hunt pass: ingest -> qualify -> autopilot -> notion ->
report, with per-node retry, error containment, and a Slack alert when a
node fails. The durable state machine stays in Postgres — nodes are
idempotent projections over it, which is why the graph runs without a
checkpointer (see the ADR for that reasoning).
"""
from __future__ import annotations

from .graph import build_hunt_graph, run_hunt_once

__all__ = ["build_hunt_graph", "run_hunt_once"]
