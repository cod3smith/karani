"""Thin Notion REST client — httpx only, same pattern as the Slack client.

Needs an internal-integration token (NOTION_TOKEN, `ntn_`/`secret_...`)
that has been granted access to the parent page / database.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionError(RuntimeError):
    pass


class NotionClient:
    def __init__(self, token: str | None = None, *, timeout: float = 20.0):
        self._token = token or os.getenv("NOTION_TOKEN", "")
        if not self._token:
            raise NotionError("NOTION_TOKEN not set. Create an internal "
                              "integration at notion.so/my-integrations and "
                              "add the token to .env.")
        self._timeout = timeout

    async def _call(self, method: str, path: str,
                    payload: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        for attempt in (1, 2):
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.request(method, f"{API_BASE}{path}",
                                         headers=headers, json=payload)
            if r.status_code == 429 and attempt == 1:
                delay = float(r.headers.get("Retry-After", "2"))
                log.warning("notion 429; retrying in %ss", delay)
                await asyncio.sleep(delay)
                continue
            if r.status_code == 404:
                raise NotionError(f"notion 404: {path}")
            if r.status_code >= 400:
                raise NotionError(
                    f"notion {r.status_code}: {r.text[:300]}"
                )
            return r.json()
        raise NotionError("notion rate-limited twice")

    async def get_database(self, database_id: str) -> dict:
        return await self._call("GET", f"/databases/{database_id}")

    async def update_database(self, database_id: str,
                              properties: dict[str, Any]) -> dict:
        return await self._call("PATCH", f"/databases/{database_id}",
                                {"properties": properties})

    async def create_database(self, parent_page_id: str, title: str,
                              properties: dict[str, Any]) -> dict:
        return await self._call("POST", "/databases", {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": properties,
        })

    async def create_page(self, database_id: str,
                          properties: dict[str, Any]) -> dict:
        return await self._call("POST", "/pages", {
            "parent": {"database_id": database_id},
            "properties": properties,
        })

    async def update_page(self, page_id: str,
                          properties: dict[str, Any]) -> dict:
        return await self._call("PATCH", f"/pages/{page_id}",
                                {"properties": properties})
