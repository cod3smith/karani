"""aijobs.net — ML/AI-specific board.

Public JSON feed at https://aijobs.net/api/jobs — remote flag + salary bands
built in. This directly targets the AI/ML segment Kelyn cares about.
"""
from __future__ import annotations

from datetime import datetime

import httpx

from .models import Job, RemoteStatus, Source
from .base import Fetcher, get_with_retry, strip_html, to_usd

# aijobs.net's JSON endpoint; supports pagination via ?page=N
FEED_URL = "https://aijobs.net/api/jobs?limit=200"


class AIJobsFetcher(Fetcher):
    source = Source.AIJOBS

    async def fetch(
        self, client: httpx.AsyncClient, slug: str | None = None,
    ) -> list[Job]:
        try:
            r = await get_with_retry(
                client, FEED_URL,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError:
            return []

        payload = r.json()
        items = payload.get("jobs") if isinstance(payload, dict) else payload
        if not items:
            return []

        jobs: list[Job] = []
        for j in items:
            job_id = str(j.get("id") or j.get("slug") or j.get("url") or "")
            if not job_id:
                continue

            title = j.get("title") or j.get("position") or ""
            company = j.get("company") or j.get("company_name") or "unknown"

            desc_html = j.get("description", "")
            desc = strip_html(desc_html)

            loc = j.get("location") or j.get("candidate_required_location") \
                or "Remote"

            remote_flag = j.get("remote") or j.get("is_remote")
            remote_status = (
                RemoteStatus.REMOTE if remote_flag else RemoteStatus.UNKNOWN
            )

            posted = None
            ts = j.get("posted_at") or j.get("date") or j.get("published_at")
            if isinstance(ts, str) and ts:
                try:
                    posted = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    pass

            lo_raw = j.get("salary_min") or j.get("min_salary")
            hi_raw = j.get("salary_max") or j.get("max_salary")
            cur = j.get("salary_currency") or "USD"
            if lo_raw:
                lo, hi, disclosed = (
                    to_usd(lo_raw, cur),
                    to_usd(hi_raw, cur) if hi_raw else None,
                    True,
                )
            else:
                lo, hi, disclosed = None, None, False

            job = Job(
                source=self.source,
                source_id=job_id,
                company=str(company).lower().replace(" ", "-"),
                company_display=company,
                title=title,
                location_raw=loc,
                remote_status=remote_status,
                description_html=desc_html,
                description_text=desc,
                apply_url=j.get("url") or j.get("apply_url") or "",
                posted_at=posted,
                comp_min_usd=lo,
                comp_max_usd=hi,
                comp_currency_original=cur.upper() if cur else None,
                comp_disclosed=disclosed,
                tags=j.get("tags") or j.get("categories") or [],
                raw=j,
            ).finalize()
            jobs.append(job)
        return jobs
