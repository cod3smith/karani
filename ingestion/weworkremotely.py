"""We Work Remotely — RSS-based (no JSON API).

Category feeds are already engineering-scoped, which is why we keep WWR.
"""
from __future__ import annotations

import re
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx

from .models import Job, RemoteStatus, Source
from .base import Fetcher, get_with_retry, strip_html

FEEDS = (
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
)

TITLE_SPLIT = re.compile(r"^(?P<company>[^:]+):\s*(?P<title>.+)$")


class WeWorkRemotelyFetcher(Fetcher):
    source = Source.WEWORKREMOTELY

    async def fetch(
        self, client: httpx.AsyncClient, slug: str | None = None,
    ) -> list[Job]:
        jobs: list[Job] = []
        seen: set[str] = set()
        for feed_url in FEEDS:
            try:
                r = await get_with_retry(client, feed_url)
            except httpx.HTTPError:
                continue

            try:
                root = ET.fromstring(r.text)
            except ET.ParseError:
                continue

            for item in root.iter("item"):
                title_raw = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc_html = (item.findtext("description") or "").strip()
                pub = item.findtext("pubDate")

                m = TITLE_SPLIT.match(title_raw)
                if m:
                    company = m.group("company").strip()
                    title = m.group("title").strip()
                else:
                    company = "unknown"
                    title = title_raw

                posted = None
                if pub:
                    try:
                        posted = parsedate_to_datetime(pub)
                    except (TypeError, ValueError):
                        pass

                # WWR uses URL slug as stable ID
                source_id = link.rstrip("/").rsplit("/", 1)[-1] or link
                if source_id in seen:
                    continue
                seen.add(source_id)

                job = Job(
                    source=self.source,
                    source_id=source_id,
                    company=company.lower().replace(" ", "-"),
                    company_display=company,
                    title=title,
                    location_raw="Remote",
                    remote_status=RemoteStatus.REMOTE,
                    description_html=desc_html,
                    description_text=strip_html(desc_html),
                    apply_url=link,
                    posted_at=posted,
                    raw={"title": title_raw, "link": link, "pubDate": pub},
                ).finalize()
                jobs.append(job)
        return jobs
