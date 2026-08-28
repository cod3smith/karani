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


def configured_channel() -> str:
    """The delivery channel — env > karani.toml [slack].channel > unset.

    Every sender must use this, never os.getenv directly: the channel
    migrated into karani.toml, and a raw env read silently disables
    delivery (the post-migration outage this helper exists to prevent).
    """
    from karani.config import get_config
    from karani.config.loader import resolve
    return resolve("SLACK_CHANNEL", get_config().slack.channel, "")


__all__ = ["SlackClient", "SlackError", "handle_command",
           "configured_channel"]
