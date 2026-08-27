"""Slack bridge — karani's two-way conversational surface.

Push: digests, fast-lane alerts, and follow-up reminders into a channel
or DM (`SlackClient`, plain httpx — no SDK needed).
Pull: a Socket Mode listener turns messages like `verdict 123 apply` or
`actions` into the same Storage/runner calls the CLI and MCP server make
(`python -m slackbridge`; requires `uv sync --extra slack`).

See docs/adrs/0010-slack-two-way-surface.md.
"""
from __future__ import annotations

from .client import SlackClient, SlackError
from .commands import handle_command

__all__ = ["SlackClient", "SlackError", "handle_command"]
