"""Remotive.com public feed.

Categories: software-dev, devops, data, ai/ml, etc.
API: https://remotive.com/api/remote-jobs?category=software-dev
Docs: https://remotive.com/api-documentation
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from .models import Job, RemoteStatus, Source
from .base import Fetcher, get_with_retry, strip_html, to_usd

FEED_BASE = "https://remotive.com/api/remote-jobs"

CATEGORIES = (
    "software-dev",
    "devops",
    "data",
    # Remotive uses `qa`, `product`, etc. — leave those off; role classifier
    # will catch stray non-eng entries from these three anyway.
)

# "$140,000 - $180,000" or "USD 100-180k" — parse a min if we can.
_SALARY_RANGE = re.compile(
    r"(?i)(?P<cur>usd|eur|gbp|kes)?\s*\$?\s*(?P<lo>\d{2,3}(?:[,.]\d{3})*|\d{2,3})"
    r"\s*[kK]?\s*(?:[-–—]|to)\s*\$?\s*(?P<hi>\d{2,3}(?:[,.]\d{3})*|\d{2,3})\s*[kK]?"
)


def _parse_salary(text: str) -> tuple[int | None, int | None, str | None, bool]:
    if not text:
        return None, None, None, False
    m = _SALARY_RANGE.search(text)
    if not m:
        return None, None, None, False
    cur = (m.group("cur") or "USD").upper()
    try:
        lo = int(m.group("lo").replace(",", "").replace(".", ""))
        hi = int(m.group("hi").replace(",", "").replace(".", ""))
    except ValueError:
        return None, None, None, False
    if lo < 1000:  # was written as k
        lo *= 1000
        hi *= 1000
    return to_usd(lo, cur), to_usd(hi, cur), cur, True


class RemotiveFetcher(Fetcher):
    source = Source.REMOTIVE

    async def fetch(
        self, client: httpx.AsyncClient, slug: str | None = None,
    ) -> list[Job]:
        jobs: list[Job] = []
        seen: set[str] = set()
        for category in CATEGORIES:
            try:
                r = await get_with_retry(
                    client, f"{FEED_BASE}?category={category}",
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError:
                continue

            payload = r.json()
            items = payload.get("jobs") if isinstance(payload, dict) else payload
            if not items:
                continue

            for j in items:
                job_id = str(j.get("id") or j.get("url") or "")
                if not job_id or job_id in seen:
                    continue
                seen.add(job_id)

                desc = strip_html(j.get("description", ""))
                loc = j.get("candidate_required_location") or "Remote"

                posted = None
                if ts := j.get("publication_date"):
                    try:
                        posted = datetime.fromisoformat(
                            ts.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                lo, hi, cur, disclosed = _parse_salary(j.get("salary", ""))

                company = j.get("company_name") or "unknown"

                job = Job(
                    source=self.source,
                    source_id=job_id,
                    company=str(company).lower().replace(" ", "-"),
                    company_display=company,
                    title=j.get("title", ""),
                    location_raw=loc,
                    remote_status=RemoteStatus.REMOTE,
                    description_html=j.get("description", ""),
                    description_text=desc,
                    apply_url=j.get("url", ""),
                    posted_at=posted,
                    comp_min_usd=lo,
                    comp_max_usd=hi,
                    comp_currency_original=cur,
                    comp_disclosed=disclosed,
                    tags=j.get("tags", []) or [],
                    raw=j,
                ).finalize()
                jobs.append(job)
        return jobs
