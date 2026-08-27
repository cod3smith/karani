"""Thin Slack Web API client — httpx only, no SDK.

The push path (chat.postMessage) must work in the daily cron with zero
optional deps; only the Socket Mode listener needs `slack-sdk`.
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://slack.com/api"


class SlackError(RuntimeError):
    pass


class SlackClient:
    def __init__(self, bot_token: str | None = None, *,
                 timeout: float = 15.0):
        self._token = bot_token or os.getenv("SLACK_BOT_TOKEN", "")
        if not self._token:
            raise SlackError("SLACK_BOT_TOKEN not set. Add it to .env.")
        self._timeout = timeout

    async def _call(self, method: str, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        for attempt in (1, 2):
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(f"{API_BASE}/{method}",
                                      headers=headers, json=payload)
            if r.status_code == 429 and attempt == 1:
                delay = int(r.headers.get("Retry-After", "2"))
                log.warning("slack 429; retrying in %ss", delay)
                await asyncio.sleep(delay)
                continue
            data = r.json()
            if not data.get("ok"):
                raise SlackError(
                    f"slack {method} failed: {data.get('error', r.status_code)}"
                )
            return data
        raise SlackError(f"slack {method} rate-limited twice")

    async def post_message(
        self, channel: str, text: str,
        blocks: list[dict] | None = None,
        thread_ts: str | None = None,
    ) -> dict:
        """`text` is the notification fallback; `blocks` the rich body."""
        payload: dict = {"channel": channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return await self._call("chat.postMessage", payload)

    async def auth_test(self) -> dict:
        return await self._call("auth.test", {})
