"""Company intel service: probe -> cache (TTL) -> dossier.

Probes are the same tolerant, byte-capped tools the qualification agent
uses (GitHub org, Wikipedia) plus a public-members lookup for warm paths.
Every probe returns text or a failure string — a dead probe degrades the
dossier, never raises out of the service.

Cache: `company_intel` table (Postgres or in-memory fallback), default
TTL 14 days. Callers get {company_display, payload, fetched_at, cached}.

Warm-path candidates are cached RAW (login, profile fields); overlap
scoring against the user's domains happens at read time in
`find_warm_paths`, so re-tuning the interest terms never requires a
re-probe. Deliberately out of scope: blog-author and conference-talk
scraping — too fragile to ship silently (roadmap 1.5.3 note).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.storage import Storage
from karani.qualification.tools import github_org, wikipedia_summary

log = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 14

_GH_MEMBERS = "https://api.github.com/orgs/{org}/public_members?per_page=12"
_GH_USER = "https://api.github.com/users/{login}"
_GH_HEADERS = {"User-Agent": "karani-intel/0.1",
               "Accept": "application/vnd.github+json"}

# Interest terms for overlap scoring: the user's own skill vocabulary.
DEFAULT_INTEREST_TERMS: tuple[str, ...] = (
    *DEFAULT_PROFILE.must_have_any,
    *getattr(DEFAULT_PROFILE, "nice_to_have", ()),
)


def _org_slug(company: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "", (company or "").lower().replace(" ", "-"))


async def _fetch_warm_candidates(company: str, *, enrich_top: int = 8) -> list[dict]:
    """Public GitHub org members — people with a reachable public presence.

    These are warm-path *candidates*: engineers at the company whose work
    is public. The first `enrich_top` get their profile fetched (name,
    bio, blog) so overlap scoring has text to work with. Karani surfaces
    them; Kelyn decides whom to contact.
    """
    slug = _org_slug(company)
    if not slug:
        return []
    try:
        async with httpx.AsyncClient(
            timeout=8.0, follow_redirects=True, headers=_GH_HEADERS,
        ) as client:
            r = await client.get(_GH_MEMBERS.format(org=slug))
            if r.status_code != 200:
                return []
            candidates = [
                {"login": m.get("login", ""),
                 "url": m.get("html_url", ""),
                 "source": "github_org_member"}
                for m in r.json()
                if m.get("login")
            ]

            async def enrich(c: dict) -> None:
                try:
                    ur = await client.get(_GH_USER.format(login=c["login"]))
                    if ur.status_code == 200:
                        u = ur.json()
                        c["name"] = u.get("name") or ""
                        c["bio"] = u.get("bio") or ""
                        c["blog"] = u.get("blog") or ""
                except Exception:
                    pass  # enrichment is best-effort

            await asyncio.gather(*(enrich(c) for c in candidates[:enrich_top]))
            return candidates
    except Exception as exc:
        log.warning("warm-candidate probe failed for %s: %s", company, exc)
        return []


def score_candidates(
    candidates: list[dict],
    interest_terms: tuple[str, ...] = DEFAULT_INTEREST_TERMS,
) -> list[dict]:
    """Overlap-score candidates against the user's domains, best first.

    Word-boundary matched over name+bio+blog. Deterministic; runs at read
    time so tuning the terms never needs a re-probe.
    """
    scored = []
    for c in candidates:
        text = " ".join([c.get("name", ""), c.get("bio", ""),
                         c.get("blog", "")]).lower()
        hits = [t for t in interest_terms
                if re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", text)]
        scored.append({**c, "warm_score": len(hits), "overlap_terms": hits})
    scored.sort(key=lambda c: (-c["warm_score"], c.get("login", "")))
    return scored


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
    await storage.save_company_intel(company, payload, now=now)
    return {"company_display": company, "payload": payload,
            "fetched_at": now, "cached": False}


async def find_warm_paths(
    storage: Storage, company: str,
    interest_terms: tuple[str, ...] = DEFAULT_INTEREST_TERMS,
) -> list[dict]:
    """Warm-path candidates via the cached dossier, overlap-ranked."""
    intel = await get_company_intel(storage, company)
    return score_candidates(intel["payload"].get("warm_candidates", []),
                            interest_terms)


def dossier_text(intel: dict) -> str:
    """Render a dossier payload as prompt-ready text."""
    p = intel.get("payload", {})
    parts: list[str] = []
    if p.get("wikipedia"):
        parts.append(f"## Background\n{p['wikipedia']}")
    if p.get("github"):
        parts.append(f"## Public engineering presence\n{p['github']}")
    candidates = score_candidates(p.get("warm_candidates") or [])
    if candidates:
        lines = []
        for c in candidates:
            overlap = (f" — overlap: {', '.join(c['overlap_terms'])}"
                       if c.get("overlap_terms") else "")
            bio = f" · {c['bio']}" if c.get("bio") else ""
            lines.append(f"- {c['login']} ({c['url']}){bio}{overlap}")
        parts.append("## Warm-path candidates (public org members, "
                     "best overlap first)\n" + "\n".join(lines))
    return "\n\n".join(parts) or "(no public intel gathered)"
