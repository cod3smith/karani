"""The candidate's own site, as profile material.

Their site, their content — no terms-of-service question, and often
the only place a project is described properly. Fetched through the
shared retry/semaphore plumbing and reduced to text; failures return
"" because a site being down must not stop a hunt.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

MAX_CHARS = 20_000
_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_MARKUP = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\n{3,}")


def html_to_text(html: str) -> str:
    text = _TAG.sub(" ", html or "")
    text = _MARKUP.sub("\n", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;?", "&", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return _SPACE.sub("\n\n", "\n".join(ln for ln in lines if ln))[:MAX_CHARS]


async def fetch_portfolio(url: str) -> str:
    if not url:
        return ""
    try:
        from karani.ingestion.base import get_with_retry
        resp = await get_with_retry(url)
        if resp is None:
            return ""
        body = resp.text if hasattr(resp, "text") else str(resp)
        return html_to_text(body)
    except Exception as exc:
        log.warning("portfolio fetch failed for %s: %s", url, exc)
        return ""
