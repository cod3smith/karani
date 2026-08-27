"""Sync logic: tracked jobs -> Notion pages, idempotent.

Page identity lives on the job row (`notion_page_id`), so sync never has
to query Notion — create once, patch thereafter. A page deleted on the
Notion side (404 on patch) is transparently recreated.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ingestion.storage import Storage

from .client import NotionClient, NotionError

log = logging.getLogger(__name__)

DATABASE_TITLE = "karani — job hunt"

# The board schema. Status mirrors the application state machine; "My
# verdict" mirrors the taste signal. Karani owns this — edit here, re-init.
DATABASE_PROPERTIES: dict[str, Any] = {
    "Application": {"title": {}},
    "Status": {"select": {"options": [
        {"name": s, "color": c} for s, c in [
            ("new", "default"), ("drafting", "yellow"), ("ready", "yellow"),
            ("applied", "blue"), ("screen", "purple"),
            ("interview", "purple"), ("offer", "green"),
            ("rejected", "red"), ("declined", "red"), ("ghosted", "gray"),
        ]
    ]}},
    "My verdict": {"select": {"options": [
        {"name": v} for v in ["apply", "shortlist", "later", "skip", "applied"]
    ]}},
    "Fit": {"number": {}},
    "Applied": {"date": {}},
    "Outcome": {"select": {"options": [
        {"name": o} for o in ["offer", "rejection", "ghosted",
                              "declined", "withdrew"]
    ]}},
    "Warm path": {"checkbox": {}},
    "Keyword coverage": {"number": {"format": "percent"}},
    "Apply link": {"url": {}},
    "Job ID": {"number": {}},
}


# Per-database schema adoption cache: database_id -> title property name.
# Sync adapts to ANY database it is pointed at: it discovers the title
# property's actual name and PATCHes in whichever karani properties are
# missing — so a hand-made board works, not just `notion init` output.
_schema_cache: dict[str, str] = {}


async def _ensure_schema(client: NotionClient, database_id: str) -> str:
    """Adopt the target database; returns its title property name."""
    if database_id in _schema_cache:
        return _schema_cache[database_id]
    db = await client.get_database(database_id)
    existing = db.get("properties", {})
    title_prop = next(
        (name for name, spec in existing.items()
         if spec.get("type") == "title"),
        "Application",
    )
    missing = {name: spec for name, spec in DATABASE_PROPERTIES.items()
               if name not in existing and "title" not in spec}
    if missing:
        log.info("notion: adding %d missing properties to %s",
                 len(missing), database_id)
        await client.update_database(database_id, missing)
    _schema_cache[database_id] = title_prop
    return title_prop


def _page_properties(row: dict, title_prop: str = "Application") -> dict[str, Any]:
    company = row.get("company_display") or row.get("company") or ""
    title = f"{company} — {row.get('title', '')}"
    applied = row.get("applied_at")
    props: dict[str, Any] = {
        title_prop: {"title": [
            {"type": "text", "text": {"content": title[:200]}}
        ]},
        "Job ID": {"number": row.get("id")},
        "Warm path": {"checkbox": bool(row.get("warm_path_used"))},
    }
    if row.get("application_status"):
        props["Status"] = {"select": {"name": row["application_status"]}}
    if row.get("user_verdict"):
        props["My verdict"] = {"select": {"name": row["user_verdict"]}}
    if row.get("fit_score") is not None:
        props["Fit"] = {"number": row["fit_score"]}
    if isinstance(applied, datetime):
        props["Applied"] = {"date": {"start": applied.date().isoformat()}}
    if row.get("outcome"):
        props["Outcome"] = {"select": {"name": row["outcome"]}}
    if row.get("draft_keyword_coverage") is not None:
        props["Keyword coverage"] = {
            "number": round(float(row["draft_keyword_coverage"]), 3)
        }
    if row.get("apply_url"):
        props["Apply link"] = {"url": row["apply_url"]}
    return props


async def init_database(client: NotionClient, parent_page_id: str) -> str:
    """Create the job-hunt database under a page; returns the database id.

    One-time setup: share the parent page with the integration first, put
    the returned id in .env as NOTION_DATABASE_ID.
    """
    db = await client.create_database(parent_page_id, DATABASE_TITLE,
                                      DATABASE_PROPERTIES)
    return db["id"]


async def sync_jobs(storage: Storage, client: NotionClient,
                    database_id: str) -> dict:
    """Upsert every tracked job onto the board. Returns counts."""
    rows = await storage.tracked_jobs()
    created = updated = errors = 0
    try:
        title_prop = await _ensure_schema(client, database_id)
    except NotionError as exc:
        log.warning("notion schema adoption failed: %s", exc)
        title_prop = "Application"
    for row in rows:
        props = _page_properties(row, title_prop)
        page_id = row.get("notion_page_id")
        try:
            if page_id:
                try:
                    await client.update_page(page_id, props)
                    updated += 1
                    continue
                except NotionError as exc:
                    if "404" not in str(exc):
                        raise
                    # Page deleted on the Notion side — recreate below.
            page = await client.create_page(database_id, props)
            await storage.set_notion_page(row["id"], page["id"])
            created += 1
        except NotionError as exc:
            errors += 1
            log.warning("notion sync failed for job %s: %s",
                        row.get("id"), exc)
    return {"tracked": len(rows), "created": created,
            "updated": updated, "errors": errors}


async def maybe_sync_job(storage: Storage, job_id: int) -> bool:
    """Best-effort single-job push after a state change.

    No-ops silently when Notion isn't configured; never raises — the
    board being briefly stale must not fail a verdict or status change.
    The daily `sync_jobs` pass reconciles anything missed here.
    """
    import os

    database_id = os.getenv("NOTION_DATABASE_ID", "")
    if not database_id or not os.getenv("NOTION_TOKEN", ""):
        return False
    try:
        row = await storage.get_job(job_id)
        if not row or not (row.get("user_verdict")
                           or row.get("application_status")):
            return False
        client = NotionClient()
        title_prop = await _ensure_schema(client, database_id)
        props = _page_properties(row, title_prop)
        page_id = row.get("notion_page_id")
        if page_id:
            try:
                await client.update_page(page_id, props)
                return True
            except NotionError as exc:
                if "404" not in str(exc):
                    raise
        page = await client.create_page(database_id, props)
        await storage.set_notion_page(row["id"], page["id"])
        return True
    except Exception as exc:
        log.warning("best-effort notion sync failed for job %s: %s",
                    job_id, exc)
        return False
