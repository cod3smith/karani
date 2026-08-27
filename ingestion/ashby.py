"""Ashby posting API."""
from __future__ import annotations

from datetime import datetime

import httpx

from .models import Job, RemoteStatus, Source
from .base import Fetcher, get_with_retry, infer_remote, strip_html, to_usd

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def _extract_comp(job: dict) -> tuple[int | None, int | None, str | None, bool]:
    """Ashby exposes several comp shapes. Handle the common ones."""
    comp = job.get("compensation") or {}

    tier = comp.get("compensationTierSummary")
    if isinstance(tier, dict):
        currency = tier.get("currencyCode")
        lo = tier.get("minValue")
        hi = tier.get("maxValue")
        if currency and lo is not None:
            return (
                to_usd(lo, currency),
                to_usd(hi, currency) if hi else None,
                currency,
                True,
            )

    tiers = comp.get("compensationTiers")
    if isinstance(tiers, list) and tiers:
        best_lo, best_hi, cur = None, None, None
        for t in tiers:
            c = t.get("currencyCode")
            lo = t.get("minValue")
            hi = t.get("maxValue")
            if c and lo is not None:
                lo_usd = to_usd(lo, c)
                hi_usd = to_usd(hi, c) if hi else None
                if lo_usd and (best_lo is None or lo_usd > best_lo):
                    best_lo, best_hi, cur = lo_usd, hi_usd, c
        if best_lo:
            return best_lo, best_hi, cur, True

    return None, None, None, False


class AshbyFetcher(Fetcher):
    source = Source.ASHBY

    async def fetch(
        self, client: httpx.AsyncClient, slug: str | None = None,
    ) -> list[Job]:
        if not slug:
            raise ValueError("ashby requires a company slug")
        r = await get_with_retry(client, BASE_URL.format(slug=slug))
        payload = r.json()

        display = payload.get("name", slug)
        jobs: list[Job] = []
        for j in payload.get("jobs", []):
            desc = strip_html(j.get("descriptionHtml", ""))
            loc = j.get("location", "") or ""

            posted = None
            if ts := j.get("publishedAt"):
                try:
                    posted = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    pass

            lo, hi, cur, disclosed = _extract_comp(j)

            remote_status = infer_remote(loc, desc)
            if j.get("isRemote"):
                remote_status = RemoteStatus.REMOTE

            job = Job(
                source=self.source,
                source_id=j["id"],
                company=slug,
                company_display=display,
                title=j["title"],
                department=j.get("department"),
                team=j.get("team"),
                location_raw=loc,
                remote_status=remote_status,
                employment_type=j.get("employmentType"),
                description_html=j.get("descriptionHtml", ""),
                description_text=desc,
                apply_url=j["jobUrl"],
                posted_at=posted,
                comp_min_usd=lo,
                comp_max_usd=hi,
                comp_currency_original=cur,
                comp_disclosed=disclosed,
                raw=j,
            ).finalize()
            jobs.append(job)
        return jobs
