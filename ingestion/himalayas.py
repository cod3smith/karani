"""Himalayas.app job feed.

Himalayas has the best country-eligibility metadata of any remote board.
Public feed: https://himalayas.app/jobs/api
Docs: https://himalayas.app/jobs/api-docs

We scope with `category=Software+Engineering` (etc.) so the feed returns only
engineering-adjacent roles. Multiple category queries are unioned.
"""
from __future__ import annotations

from datetime import datetime

import httpx

from .models import Job, RemoteStatus, Source
from .base import Fetcher, get_with_retry, strip_html, to_usd

FEED_BASE = "https://himalayas.app/jobs/api"

CATEGORIES = (
    "Software Engineering",
    "DevOps",
    "Data",
    "Machine Learning",
    "AI",
    "Security",
)


def _company_display(item: dict) -> str:
    c = item.get("company")
    if isinstance(c, dict):
        return c.get("name") or item.get("companyName") or "unknown"
    return item.get("companyName") or c or "unknown"


def _resolve_comp(item: dict) -> tuple[int | None, int | None, str | None, bool]:
    lo = item.get("minSalary") or item.get("salaryMin")
    hi = item.get("maxSalary") or item.get("salaryMax")
    cur = item.get("salaryCurrency") or item.get("currency") or "USD"
    if not lo:
        return None, None, None, False
    return (
        to_usd(lo, cur),
        to_usd(hi, cur) if hi else None,
        cur.upper() if cur else None,
        True,
    )


class HimalayasFetcher(Fetcher):
    source = Source.HIMALAYAS

    async def fetch(
        self, client: httpx.AsyncClient, slug: str | None = None,
    ) -> list[Job]:
        # Union across the eng-adjacent categories. De-dup within this call
        # since Himalayas returns overlapping items across categories.
        seen: set[str] = set()
        jobs: list[Job] = []

        for category in CATEGORIES:
            params = f"?category={category.replace(' ', '+')}"
            try:
                r = await get_with_retry(
                    client, FEED_BASE + params,
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError:
                continue

            payload = r.json()
            items = payload.get("jobs") if isinstance(payload, dict) else payload
            if not items:
                continue

            for j in items:
                job_id = str(
                    j.get("guid") or j.get("id") or j.get("slug", "") or ""
                )
                if not job_id or job_id in seen:
                    continue
                seen.add(job_id)

                desc = j.get("excerpt") or strip_html(j.get("description", ""))
                countries = j.get("locationRestrictions") or j.get("countries") or []
                loc_raw = ", ".join(countries) if countries else "Remote"

                posted = None
                ts = j.get("pubDate") or j.get("publishedDate")
                if isinstance(ts, str) and ts:
                    try:
                        posted = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except ValueError:
                        pass

                lo, hi, cur, disclosed = _resolve_comp(j)
                company = _company_display(j)

                job = Job(
                    source=self.source,
                    source_id=job_id,
                    company=str(company).lower().replace(" ", "-"),
                    company_display=company,
                    title=j.get("title", ""),
                    location_raw=loc_raw,
                    location_normalized=[c.lower() for c in countries] if countries else [],
                    remote_status=RemoteStatus.REMOTE,
                    description_html=j.get("description", ""),
                    description_text=desc,
                    apply_url=j.get("applicationLink") or j.get("url", ""),
                    posted_at=posted,
                    comp_min_usd=lo,
                    comp_max_usd=hi,
                    comp_currency_original=cur,
                    comp_disclosed=disclosed,
                    tags=j.get("categories") or j.get("tags", []) or [],
                    raw=j,
                ).finalize()
                jobs.append(job)
        return jobs
