"""Workable public account widget API.

Per company: https://apply.workable.com/api/v1/widget/accounts/{slug}
That returns a list; each posting has a shortcode we can fetch for detail:
https://apply.workable.com/api/v3/accounts/{slug}/jobs/{shortcode}

To keep the fetch cheap we take the list payload and only pull details for
the postings that look engineering-adjacent by title (regex prefilter).
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from .models import Job, RemoteStatus, Source
from .base import Fetcher, get_with_retry, infer_remote, strip_html, to_usd

LIST_URL = "https://apply.workable.com/api/v1/widget/accounts/{slug}"
DETAIL_URL = (
    "https://apply.workable.com/api/v3/accounts/{slug}/jobs/{shortcode}"
)


_ENG_TITLE = re.compile(
    r"(?i)\b(engineer|developer|swe|programmer|scientist|sre|devops|"
    r"ml|ai|data|security|infrastructure|platform|architect|"
    r"researcher|technical lead|tech lead)\b"
)


def _looks_engineering(title: str) -> bool:
    return bool(_ENG_TITLE.search(title))


def _extract_comp(detail: dict) -> tuple[int | None, int | None, str | None, bool]:
    salary = detail.get("salary") or {}
    lo = salary.get("salaryFrom") or salary.get("min")
    hi = salary.get("salaryTo") or salary.get("max")
    cur = salary.get("salaryCurrency") or salary.get("currency")
    if not lo:
        return None, None, None, False
    return (
        to_usd(lo, cur),
        to_usd(hi, cur) if hi else None,
        cur.upper() if cur else None,
        True,
    )


class WorkableFetcher(Fetcher):
    source = Source.WORKABLE

    async def fetch(
        self, client: httpx.AsyncClient, slug: str | None = None,
    ) -> list[Job]:
        if not slug:
            raise ValueError("workable requires a company slug")
        try:
            r = await get_with_retry(client, LIST_URL.format(slug=slug))
        except httpx.HTTPError:
            return []
        payload = r.json()
        postings = payload.get("jobs") or payload.get("results") or []

        display = payload.get("name") or slug

        jobs: list[Job] = []
        for p in postings:
            title = p.get("title", "")
            if not _looks_engineering(title):
                continue

            shortcode = p.get("shortcode") or p.get("id")
            if not shortcode:
                continue

            # Fetch detail so we get description + comp. Best-effort — if a
            # single detail fetch fails we skip that posting.
            try:
                d = await get_with_retry(
                    client, DETAIL_URL.format(slug=slug, shortcode=shortcode),
                )
                detail = d.json()
            except httpx.HTTPError:
                continue

            desc_html = detail.get("description") or ""
            desc = strip_html(desc_html)

            loc_parts = []
            location = detail.get("location") or {}
            for k in ("city", "region", "country"):
                v = location.get(k)
                if v:
                    loc_parts.append(v)
            loc_raw = ", ".join(loc_parts) or "Remote"

            remote_status = infer_remote(loc_raw, desc)
            if detail.get("telecommuting") or location.get("telecommuting"):
                remote_status = RemoteStatus.REMOTE

            posted = None
            if ts := detail.get("published_on") or detail.get("created_at"):
                try:
                    posted = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    pass

            lo, hi, cur, disclosed = _extract_comp(detail)

            job = Job(
                source=self.source,
                source_id=str(shortcode),
                company=slug,
                company_display=display,
                title=title,
                department=detail.get("department"),
                team=detail.get("function"),
                location_raw=loc_raw,
                remote_status=remote_status,
                employment_type=detail.get("employment_type"),
                description_html=desc_html,
                description_text=desc,
                apply_url=detail.get("application_url") or detail.get("url") or "",
                posted_at=posted,
                comp_min_usd=lo,
                comp_max_usd=hi,
                comp_currency_original=cur,
                comp_disclosed=disclosed,
                raw=detail,
            ).finalize()
            jobs.append(job)
        return jobs
