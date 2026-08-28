"""Notion mirror — the job-hunt board that updates itself.

One Notion database page per tracked job (anything with a user verdict or
an application status). Karani owns the database schema (`init_database`)
and upserts pages on every sync, so the board stays current from cron
with no human in the loop. See docs/adrs/0011-notion-mirror.md.
"""
from __future__ import annotations

from .client import NotionClient, NotionError
from .sync import init_database, maybe_sync_job, sync_jobs

def configured_database_id() -> str:
    """Board id — env > karani.toml [notion].database_id > unset."""
    from karani.config import get_config
    from karani.config.loader import resolve
    return resolve("NOTION_DATABASE_ID", get_config().notion.database_id, "")


__all__ = ["NotionClient", "NotionError", "init_database", "sync_jobs",
           "maybe_sync_job", "configured_database_id"]
