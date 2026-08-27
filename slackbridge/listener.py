"""Socket Mode listener — the inbound half of the two-way bridge.

Requires `uv sync --extra slack` (slack-sdk) plus a Slack app with:
- Socket Mode enabled (app-level token with `connections:write` →
  SLACK_APP_TOKEN, `xapp-...`)
- Bot token scopes: `chat:write`, `im:history` (+ `channels:history` if
  used in a channel) → SLACK_BOT_TOKEN (`xoxb-...`)
- Event subscriptions: `message.im` (+ `message.channels` if needed)

Every user message becomes a `handle_command` call; the reply posts back
to the same conversation. `SLACK_CHANNEL`, when set, restricts which
conversation the bridge listens to.
"""
from __future__ import annotations

import asyncio
import logging
import os

from ingestion.config import settings
from ingestion.storage import Storage
from memory import MemoryManager

from .client import SlackClient
from .commands import handle_command
from .interactions import handle_interaction

log = logging.getLogger(__name__)


def _is_command_message(event: dict, *, channel_filter: str | None) -> bool:
    """Only plain user messages count — never bots (loop protection),
    never edits/joins (subtypes), and only the configured conversation."""
    if event.get("type") != "message":
        return False
    if event.get("bot_id") or event.get("subtype"):
        return False
    if not (event.get("text") or "").strip():
        return False
    if channel_filter and event.get("channel") != channel_filter:
        return False
    return True


async def handle_event(
    event: dict, *,
    storage: Storage,
    memory: MemoryManager,
    slack: SlackClient,
    channel_filter: str | None = None,
) -> str | None:
    """Process one Events API `message` event; returns the reply text."""
    if not _is_command_message(event, channel_filter=channel_filter):
        return None
    reply = await handle_command(event["text"], storage=storage,
                                 memory=memory)
    await slack.post_message(event["channel"], reply,
                             thread_ts=event.get("thread_ts"))
    return reply


async def run_listener() -> None:
    """Connect over Socket Mode and serve until interrupted."""
    try:
        from slack_sdk.socket_mode.aiohttp import SocketModeClient
        from slack_sdk.socket_mode.request import SocketModeRequest
        from slack_sdk.socket_mode.response import SocketModeResponse
        from slack_sdk.web.async_client import AsyncWebClient
    except ImportError as exc:  # optional dep, same pattern as anthropic/mem0
        raise RuntimeError(
            "slack-sdk not installed. Run `uv sync --extra slack`."
        ) from exc

    app_token = os.getenv("SLACK_APP_TOKEN", "")
    bot_token = os.getenv("SLACK_BOT_TOKEN", "")
    # Fail fast on token-type mix-ups: apps.connections.open only accepts
    # app-level tokens, and slack-sdk's error for the wrong type is a
    # confusing retry loop ('not_allowed_token_type').
    if not app_token:
        raise RuntimeError("SLACK_APP_TOKEN not set (xapp-..., Socket Mode).")
    if not app_token.startswith("xapp-"):
        raise RuntimeError(
            "SLACK_APP_TOKEN must be an app-level token (xapp-...) with the "
            "connections:write scope — generate one under Basic Information "
            "-> App-Level Tokens. It looks like a different token type is "
            "set (bot tokens start with xoxb-)."
        )
    if not bot_token.startswith("xoxb-"):
        raise RuntimeError(
            "SLACK_BOT_TOKEN must be a Bot User OAuth Token (xoxb-...) — "
            "copy it from OAuth & Permissions after installing the app."
        )

    storage = Storage(settings.database_url)
    await storage.connect()
    memory = MemoryManager(storage)
    slack = SlackClient()
    channel_filter = os.getenv("SLACK_CHANNEL") or None

    sm_client = SocketModeClient(
        app_token=app_token,
        web_client=AsyncWebClient(token=os.getenv("SLACK_BOT_TOKEN", "")),
    )

    async def _on_request(client: SocketModeClient,
                          req: SocketModeRequest) -> None:
        # Ack first — Slack redelivers un-acked envelopes, which would
        # double-execute billed commands like `draft`.
        await client.send_socket_mode_response(
            SocketModeResponse(envelope_id=req.envelope_id)
        )
        try:
            if req.type == "events_api":
                event = (req.payload or {}).get("event") or {}
                await handle_event(event, storage=storage, memory=memory,
                                   slack=slack,
                                   channel_filter=channel_filter)
            elif req.type == "interactive":
                # Pack review buttons (ADR 0012). Reply in the pack's
                # thread so the card and its resolution stay together.
                payload = req.payload or {}
                reply = await handle_interaction(payload, storage=storage,
                                                 memory=memory)
                if reply:
                    chan = (payload.get("channel") or {}).get("id", "")
                    ts = (payload.get("message") or {}).get("ts")
                    if chan:
                        await slack.post_message(chan, reply, thread_ts=ts)
        except Exception:
            log.exception("slack request handling failed (%s)", req.type)

    sm_client.socket_mode_request_listeners.append(_on_request)
    await sm_client.connect()
    log.info("slack listener connected (channel filter: %s)",
             channel_filter or "none")
    try:
        await asyncio.Event().wait()  # serve forever
    finally:
        await storage.close()
