"""Fan-out must match what the provider can actually serve.

Regression: qualification fanned out 3 rows at a time while a local
Ollama pinned to one slot served them one at a time. Rows 2 and 3 sat
in the server queue burning the 600s client timeout, failed with
ReadTimeout, and each retried 3x — 17 of 30 rows failed and one hourly
pass took 13.5 hours.
"""
from __future__ import annotations

import asyncio

import pytest

from karani.ingestion.resume import ResumeProfile
from karani.ingestion.storage import Storage
from karani.qualification.runner import resolve_concurrency


class SerialOnlyClient:
    """Mimics a single-slot local server: records max overlap."""
    model_name = "qwen3:4b"
    max_parallel = 1

    def __init__(self):
        self.inflight = 0
        self.peak = 0

    async def complete(self, system, user):
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        await asyncio.sleep(0)
        self.inflight -= 1
        return '{"fit_score": 10, "verdict": "skip", "why_apply": "",' \
               ' "why_skip": "no", "recommended_positioning": ""}'


class HostedClient(SerialOnlyClient):
    model_name = "moonshotai/kimi-k2-thinking"
    max_parallel = 3


def test_local_provider_declares_single_slot():
    from karani.qualification.local import LocalQualifier
    from karani.qualification.openrouter import OpenRouterQualifier
    assert LocalQualifier.max_parallel == 1
    # Hosted providers keep fanning out — they serve requests in parallel.
    assert OpenRouterQualifier.max_parallel > 1


def test_resolve_concurrency_prefers_explicit_then_client_then_default():
    assert resolve_concurrency(5, SerialOnlyClient()) == 5   # explicit wins
    assert resolve_concurrency(None, SerialOnlyClient()) == 1
    assert resolve_concurrency(None, HostedClient()) == 3
    assert resolve_concurrency(None, object()) == 3          # unknown client


async def _seed(storage: Storage, n: int) -> None:
    from karani.ingestion.filters import pre_filter
    from karani.ingestion.models import Job, RemoteStatus, Source
    from karani.ingestion.profile import DEFAULT_PROFILE
    for i in range(n):
        job = Job(
            source=Source.GREENHOUSE, source_id=str(i),
            company="acme", company_display="Acme",
            title="Senior Backend Engineer",
            location_raw="Remote", remote_status=RemoteStatus.REMOTE,
            description_text=("We hire globally. Python and Kafka required. "
                              "Salary: $180,000 - $220,000. "
                              "Same pay regardless of location."),
            apply_url=f"https://example.com/{i}",
        ).finalize()
        await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))


@pytest.mark.asyncio
async def test_single_slot_client_is_never_overlapped():
    from karani.qualification.runner import qualify_pending

    storage = Storage("")
    await storage.connect()
    await _seed(storage, 6)
    client = SerialOnlyClient()

    stats = await qualify_pending(storage, client,
                                  ResumeProfile(raw_markdown="# K"), limit=6)
    assert stats.fetched == 6
    assert client.peak == 1, "queued rows time out behind a one-slot server"


@pytest.mark.asyncio
async def test_hosted_client_still_fans_out():
    from karani.qualification.runner import qualify_pending

    storage = Storage("")
    await storage.connect()
    await _seed(storage, 6)
    client = HostedClient()
    await qualify_pending(storage, client,
                          ResumeProfile(raw_markdown="# K"), limit=6)
    assert client.peak > 1


# --- batch size also follows the provider ---

def test_batch_size_clamps_to_what_a_slow_provider_can_finish():
    """qualify_limit is sized for a hosted model (seconds per row). If
    the budget guard swaps in the local model mid-month, a 50-row batch
    becomes a two-hour pass and the hourly cadence is gone."""
    from karani.qualification.runner import resolve_limit
    from karani.qualification.local import LocalQualifier

    assert resolve_limit(50, LocalQualifier.__new__(LocalQualifier)) == 15
    assert resolve_limit(10, LocalQualifier.__new__(LocalQualifier)) == 10
    assert resolve_limit(50, object()) == 50       # hosted: no clamp
