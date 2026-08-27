"""Reverse-ATS probe — given a company name, figure out which ATS they're on.

Called after each qualify pass on companies that (a) surfaced from a feed
source and (b) don't already have a TARGETS entry. If a probe hits, we can
promote the company to per-slug polling next run.

Probes are cheap (single HEAD/GET per ATS) and cached in the DB. Rate-
limited via the shared per-host semaphore from base.py.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


# Given a company name like "GitLab Inc.", produce candidate slugs like
# "gitlab", "gitlab-inc". Different ATS platforms follow different conventions.
def slug_candidates(company: str) -> list[str]:
    base = re.sub(r"\s*(inc|inc\.|llc|corp|corporation|ltd|ltd\.|gmbh|ag|co)\s*$",
                  "", (company or "").lower()).strip()
    tokens = re.findall(r"[a-z0-9]+", base)
    if not tokens:
        return []
    out = [
        "".join(tokens),
        "-".join(tokens),
    ]
    # Also try the first token only for single-word brand names.
    if len(tokens) > 1:
        out.append(tokens[0])
    # Deduplicate preserving order.
    seen: set[str] = set()
    return [s for s in out if not (s in seen or seen.add(s))]


# Per-ATS probe URLs. Return True if the slug likely exists.
_PROBES: dict[str, str] = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever":      "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby":      "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "workable":   "https://apply.workable.com/api/v1/widget/accounts/{slug}",
}


@dataclass
class ProbeResult:
    ats: str
    slug: str
    hits: bool


async def _probe_one(client: httpx.AsyncClient, ats: str, slug: str) -> ProbeResult:
    url = _PROBES[ats].format(slug=slug)
    try:
        r = await client.get(url, timeout=8.0)
        return ProbeResult(ats=ats, slug=slug, hits=r.status_code == 200)
    except httpx.HTTPError:
        return ProbeResult(ats=ats, slug=slug, hits=False)


async def probe_company(company: str) -> dict:
    """Try every (ATS × slug) combo. Return {ats: {slug: hit}} plus best guess."""
    candidates = slug_candidates(company)
    if not candidates:
        return {"probed": False, "reason": "no candidate slugs"}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [
            _probe_one(client, ats, slug)
            for ats in _PROBES
            for slug in candidates
        ]
        results = await asyncio.gather(*tasks)

    by_ats: dict[str, dict[str, bool]] = {}
    for r in results:
        by_ats.setdefault(r.ats, {})[r.slug] = r.hits

    # Pick the first hit. Prefer Greenhouse → Ashby → Lever → Workable
    # (rough ranking of coverage + payload quality).
    order = ["greenhouse", "ashby", "lever", "workable"]
    winner_ats: str | None = None
    winner_slug: str | None = None
    for ats in order:
        for slug in candidates:
            if by_ats.get(ats, {}).get(slug):
                winner_ats, winner_slug = ats, slug
                break
        if winner_ats:
            break

    return {
        "probed": True,
        "candidates": candidates,
        "results": by_ats,
        "ats": winner_ats,
        "slug": winner_slug,
    }


async def probe_unpromoted(storage, limit: int = 10) -> list[dict]:
    """Probe up to `limit` unprobed discovered companies and update the DB."""
    unpromoted = await storage.unprobed_companies(limit=limit)
    outcomes: list[dict] = []
    for row in unpromoted:
        result = await probe_company(row["company_display"])
        await storage.record_probe(
            discovered_id=row["id"],
            probe_results=result,
            ats_source=result.get("ats"),
            ats_slug=result.get("slug"),
        )
        outcomes.append({
            "company": row["company_display"],
            "ats": result.get("ats"),
            "slug": result.get("slug"),
        })
    return outcomes
