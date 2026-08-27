"""Shared helpers for source fetchers."""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    AsyncRetrying, RetryError, retry_if_exception_type,
    stop_after_attempt, wait_exponential,
)

from .config import settings
from .models import RemoteStatus

log = logging.getLogger(__name__)


def strip_html(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def infer_remote(location: str, description: str) -> RemoteStatus:
    text = f"{location} {description}".lower()
    if any(s in text for s in ("hybrid",)):
        return RemoteStatus.HYBRID
    if any(s in text for s in (
        "remote", "work from anywhere", "distributed", "wfh"
    )):
        return RemoteStatus.REMOTE
    if any(s in text for s in ("on-site", "onsite", "in office", "in-office")):
        return RemoteStatus.ONSITE
    return RemoteStatus.UNKNOWN


def to_usd(amount: float | int | None, currency: str | None) -> int | None:
    if amount is None or not currency:
        return None
    rate = settings.fx_to_usd.get(currency.upper())
    if rate is None:
        return None
    return int(amount * rate)


# --- Per-host semaphores prevent one slow ATS from starving others. ---
_HOST_SEMAPHORES: dict[str, asyncio.Semaphore] = defaultdict(
    lambda: asyncio.Semaphore(settings.http_per_host_concurrency)
)


def _host_semaphore(url: str) -> asyncio.Semaphore:
    host = urlparse(url).hostname or "default"
    return _HOST_SEMAPHORES[host]


# --- Retry helper: exponential backoff on 5xx/429/network errors. ---

_RETRY_STATUS = {429, 500, 502, 503, 504}


class RetryableStatusError(httpx.HTTPStatusError):
    pass


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_attempts: int = 4,
) -> httpx.Response:
    """GET with per-host concurrency + exponential backoff on 5xx/429.

    404 fails fast — a slug going 404 shouldn't hold the semaphore.
    """
    sem = _host_semaphore(url)
    async with sem:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max_attempts),
                wait=wait_exponential(multiplier=1, min=1, max=20),
                retry=retry_if_exception_type(
                    (httpx.TransportError, RetryableStatusError)
                ),
                reraise=True,
            ):
                with attempt:
                    r = await client.get(url, headers=headers or {})
                    if r.status_code in _RETRY_STATUS:
                        # tenacity will retry
                        raise RetryableStatusError(
                            f"retryable {r.status_code}", request=r.request,
                            response=r,
                        )
                    r.raise_for_status()
                    return r
        except RetryError as e:
            raise e.last_attempt.exception()  # type: ignore[misc]
    # Unreachable — AsyncRetrying either returns or raises
    raise RuntimeError("get_with_retry fell through")


class Fetcher(ABC):
    """One instance per source. Stateless — the client is passed in."""

    source: object  # subclasses override with a Source enum value

    @abstractmethod
    async def fetch(
        self, client: httpx.AsyncClient, slug: str | None = None,
    ) -> list:
        ...
