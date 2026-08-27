"""Company intel service: probe -> cache (TTL) -> dossier.

Probes are the same tolerant, byte-capped tools the qualification agent
uses (GitHub org, Wikipedia) plus a public-members lookup for warm paths.
Every probe returns text or a failure string — a dead probe degrades the
dossier, never raises out of the service.

Cache: `company_intel` table (Postgres or in-memory fallback), default
TTL 14 days. Callers get {company_display, payload, fetched_at, cached}.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from ingestion.storage import Storage
from qualification.tools import github_org, wikipedia_summary

log = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 14

_GH_MEMBERS = "https://api.github.com/orgs/{org}/public_members?per_page=12"
_GH_USER = "https://api.github.com/users/{login}"


def _org_slug(company: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "", (company or "").lower().replace(" ", "-"))


async def _fetch_warm_candidates(company: str) -> list[dict]:
    """Public GitHub org members — people with a reachable public presence.

    These are warm-path *candidates*: engineers at the company whose work
    is public. Karani surfaces them; Kelyn decides whom to contact.
    """
    slug = _org_slug(company)
    if not slug:
        return []
    try:
        async with httpx.AsyncClient(
            timeout=8.0, follow_redirects=True,
            headers={"User-Agent": "karani-intel/0.1",
                     "Accept": "application/vnd.github+json"},
        ) as client:
            r = await client.get(_GH_MEMBERS.format(org=slug))
            if r.status_code != 200:
                return []
            return [
                {"login": m.get("login", ""),
                 "url": m.get("html_url", ""),
                 "source": "github_org_member"}
                for m in r.json()
                if m.get("login")
            ]
    except Exception as exc:
        log.warning("warm-candidate probe failed for %s: %s", company, exc)
        return []


async def _build_payload(company: str) -> dict:
    slug = _org_slug(company)
    return {
        "github": await github_org(slug),
        "wikipedia": await wikipedia_summary(company),
        "warm_candidates": await _fetch_warm_candidates(company),
        "org_slug": slug,
    }


async def get_company_intel(
    storage: Storage, company: str, *,
    ttl_days: int = DEFAULT_TTL_DAYS,
    force_refresh: bool = False,
    now: datetime | None = None,
) -> dict:
    """Cached dossier for `company`; probes and refreshes when stale."""
    now = now or datetime.now(timezone.utc)
    if not force_refresh:
        cached = await storage.get_company_intel(company)
        if cached:
            fetched_at = cached.get("fetched_at")
            if isinstance(fetched_at, datetime):
                if fetched_at.tzinfo is None:
                    fetched_at = fetched_at.replace(tzinfo=timezone.utc)
                if now - fetched_at < timedelta(days=ttl_days):
                    return {**cached, "cached": True}

    payload = await _build_payload(company)
    await storage.save_company_intel(company, payload)
    return {"company_display": company, "payload": payload,
            "fetched_at": now, "cached": False}


async def find_warm_paths(storage: Storage, company: str) -> list[dict]:
    """Warm-path candidates for a company, via the cached dossier."""
    intel = await get_company_intel(storage, company)
    return intel["payload"].get("warm_candidates", [])


def dossier_text(intel: dict) -> str:
    """Render a dossier payload as prompt-ready text."""
    p = intel.get("payload", {})
    parts: list[str] = []
    if p.get("wikipedia"):
        parts.append(f"## Background\n{p['wikipedia']}")
    if p.get("github"):
        parts.append(f"## Public engineering presence\n{p['github']}")
    candidates = p.get("warm_candidates") or []
    if candidates:
        lines = "\n".join(f"- {c['login']} ({c['url']})" for c in candidates)
        parts.append(f"## Warm-path candidates (public org members)\n{lines}")
    return "\n\n".join(parts) or "(no public intel gathered)"
