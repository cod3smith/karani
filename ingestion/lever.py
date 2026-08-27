"""Lever public postings API."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from .models import Job, Source
from .base import Fetcher, get_with_retry, infer_remote, strip_html

BASE_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"


class LeverFetcher(Fetcher):
    source = Source.LEVER

    async def fetch(
        self, client: httpx.AsyncClient, slug: str | None = None,
    ) -> list[Job]:
        if not slug:
            raise ValueError("lever requires a company slug")
        r = await get_with_retry(client, BASE_URL.format(slug=slug))
        listings = r.json()

        jobs: list[Job] = []
        for j in listings:
            cats = j.get("categories", {}) or {}
            desc_plain = j.get("descriptionPlain") or ""
            desc = desc_plain or strip_html(j.get("description", ""))
            loc = cats.get("location", "") or ""

            all_locations = cats.get("allLocations") or []
            loc_bag = " ".join(all_locations + [loc]).lower()
            remote_status = infer_remote(loc_bag, desc)

            posted = None
            if ts := j.get("createdAt"):
                try:
                    posted = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                except (ValueError, OSError):
                    pass

            job = Job(
                source=self.source,
                source_id=j["id"],
                company=slug,
                company_display=slug,
                title=j["text"],
                department=cats.get("department"),
                team=cats.get("team"),
                location_raw=loc,
                remote_status=remote_status,
                employment_type=cats.get("commitment"),
                description_html=j.get("description", ""),
                description_text=desc,
                apply_url=j["hostedUrl"],
                posted_at=posted,
                raw=j,
            ).finalize()
            jobs.append(job)
        return jobs
