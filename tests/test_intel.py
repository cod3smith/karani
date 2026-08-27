"""Company intel: caching + TTL, warm-path parsing, dossier rendering."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import intel.service as svc
from ingestion.storage import Storage

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake_probes(monkeypatch):
    calls = {"n": 0}

    async def fake_github(org):
        calls["n"] += 1
        return f"**{org}** — infra tools"

    async def fake_wiki(topic):
        return f"{topic} is a devops company."

    async def fake_members(company):
        return [{"login": "alice", "url": "https://github.com/alice",
                 "source": "github_org_member"}]

    monkeypatch.setattr(svc, "github_org", fake_github)
    monkeypatch.setattr(svc, "wikipedia_summary", fake_wiki)
    monkeypatch.setattr(svc, "_fetch_warm_candidates", fake_members)
    return calls


@pytest.mark.asyncio
async def test_intel_fetch_then_cache(fake_probes):
    storage = Storage("")
    await storage.connect()

    first = await svc.get_company_intel(storage, "GitLab", now=NOW)
    assert first["cached"] is False
    assert fake_probes["n"] == 1
    assert first["payload"]["warm_candidates"][0]["login"] == "alice"

    second = await svc.get_company_intel(storage, "GitLab", now=NOW)
    assert second["cached"] is True
    assert fake_probes["n"] == 1  # no re-probe within TTL


@pytest.mark.asyncio
async def test_intel_ttl_expiry_reprobes(fake_probes):
    storage = Storage("")
    await storage.connect()
    await svc.get_company_intel(storage, "GitLab", now=NOW)
    later = NOW + timedelta(days=15)  # past the 14-day TTL
    refreshed = await svc.get_company_intel(storage, "GitLab", now=later)
    assert refreshed["cached"] is False
    assert fake_probes["n"] == 2


@pytest.mark.asyncio
async def test_find_warm_paths(fake_probes):
    storage = Storage("")
    await storage.connect()
    paths = await svc.find_warm_paths(storage, "GitLab")
    assert paths == [{"login": "alice", "url": "https://github.com/alice",
                      "source": "github_org_member"}]


def test_dossier_text_renders_sections():
    text = svc.dossier_text({"payload": {
        "wikipedia": "A devops company.",
        "github": "**gitlab** — repos",
        "warm_candidates": [{"login": "alice",
                             "url": "https://github.com/alice"}],
    }})
    assert "## Background" in text
    assert "## Public engineering presence" in text
    assert "alice" in text
    assert svc.dossier_text({"payload": {}}) == "(no public intel gathered)"


def test_org_slug():
    assert svc._org_slug("Hugging Face") == "hugging-face"
    assert svc._org_slug("GitLab") == "gitlab"
