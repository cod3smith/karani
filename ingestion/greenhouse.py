"""Greenhouse public job board API."""
from __future__ import annotations

from datetime import datetime

import httpx

from .models import Job, Source
from .base import Fetcher, get_with_retry, infer_remote, strip_html

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


class GreenhouseFetcher(Fetcher):
    source = Source.GREENHOUSE

    async def fetch(
        self, client: httpx.AsyncClient, slug: str | None = None,
    ) -> list[Job]:
        if not slug:
            raise ValueError("greenhouse requires a company slug")
        r = await get_with_retry(client, BASE_URL.format(slug=slug))
        payload = r.json()

        jobs: list[Job] = []
        for j in payload.get("jobs", []):
            desc = strip_html(j.get("content", ""))
            loc = (j.get("location") or {}).get("name", "") or ""

            posted = None
            if ts := j.get("updated_at"):
                try:
                    posted = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    pass

            dept = None
            depts = j.get("departments") or []
            if depts:
                dept = depts[0].get("name")

            offices = j.get("offices") or []
            office_bag = " ".join(o.get("name", "") for o in offices).lower()
            remote_hint = f"{loc} {office_bag}"

            job = Job(
                source=self.source,
                source_id=str(j["id"]),
                company=slug,
                company_display=j.get("company_name") or slug,
                title=j["title"],
                department=dept,
                location_raw=loc,
                remote_status=infer_remote(remote_hint, desc),
                description_html=j.get("content", ""),
                description_text=desc,
                apply_url=j["absolute_url"],
                posted_at=posted,
                raw=j,
            ).finalize()
            jobs.append(job)
        return jobs
