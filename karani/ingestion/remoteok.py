"""Remote OK JSON feed. https://remoteok.com/api

Item 0 is metadata (legal notice); real jobs start at index 1.

We scope with `?tags=dev,python,ml,ai,backend,devops,data,engineer` to reduce
the sales/marketing/design noise the raw feed floods you with.
"""
from __future__ import annotations

from datetime import datetime

import httpx

from .config import settings
from .models import Job, RemoteStatus, Source
from .base import Fetcher, get_with_retry, strip_html, to_usd

# RemoteOK's tag filter is best-effort — many roles omit tags. We still sweep
# and let the pre-filter drop non-engineering entries.
FEED_URL = (
    "https://remoteok.com/api"
    "?tags=dev,python,javascript,typescript,ml,ai,machine-learning,"
    "backend,devops,data,engineer,golang,rust,react"
)

# When the feed hands us a "salary_min" without a currency, assume USD only
# if the number is in a plausible US-band range. Otherwise leave undisclosed.
_USD_PLAUSIBLE_MIN = 30_000
_USD_PLAUSIBLE_MAX = 1_000_000


def _resolve_comp(item: dict) -> tuple[int | None, int | None, str | None, bool]:
    lo_raw = item.get("salary_min")
    hi_raw = item.get("salary_max")
    if not lo_raw:
        return None, None, None, False

    currency = item.get("salary_currency") or item.get("currency")
    if currency:
        return (
            to_usd(lo_raw, currency),
            to_usd(hi_raw, currency) if hi_raw else None,
            currency.upper(),
            True,
        )
    # No currency — accept as USD only if plausible.
    try:
        lo = int(lo_raw)
    except (TypeError, ValueError):
        return None, None, None, False
    if not (_USD_PLAUSIBLE_MIN <= lo <= _USD_PLAUSIBLE_MAX):
        return None, None, None, False
    hi = None
    if hi_raw:
        try:
            hi = int(hi_raw)
        except (TypeError, ValueError):
            hi = None
    return lo, hi, "USD", True


def _company_display(item: dict) -> str:
    c = item.get("company")
    if isinstance(c, dict):
        return c.get("name") or "unknown"
    return c or "unknown"


class RemoteOKFetcher(Fetcher):
    source = Source.REMOTEOK

    async def fetch(
        self, client: httpx.AsyncClient, slug: str | None = None,
    ) -> list[Job]:
        r = await get_with_retry(
            client, FEED_URL, headers={"Accept": "application/json"},
        )
        items = r.json()

        jobs: list[Job] = []
        for j in items[1:]:  # skip metadata
            if not isinstance(j, dict) or "id" not in j:
                continue

            desc = strip_html(j.get("description", ""))
            loc = j.get("location", "") or "Remote"

            posted = None
            if ts := j.get("date"):
                try:
                    posted = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    pass

            lo, hi, cur, disclosed = _resolve_comp(j)

            company = _company_display(j)

            job = Job(
                source=self.source,
                source_id=str(j["id"]),
                company=str(company).lower().replace(" ", "-"),
                company_display=company,
                title=j.get("position", ""),
                location_raw=loc,
                remote_status=RemoteStatus.REMOTE,
                description_html=j.get("description", ""),
                description_text=desc,
                apply_url=j.get("url") or j.get("apply_url", ""),
                posted_at=posted,
                comp_min_usd=lo,
                comp_max_usd=hi,
                comp_currency_original=cur,
                comp_disclosed=disclosed,
                tags=j.get("tags", []) or [],
                raw=j,
            ).finalize()
            jobs.append(job)
        _ = settings  # keep reference for future config-driven tag list
        return jobs
