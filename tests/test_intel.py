"""Company intel: caching + TTL, warm-path parsing, dossier rendering."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import karani.intel.service as svc
from karani.ingestion.storage import Storage

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
async def test_find_warm_paths_scored(fake_probes):
    storage = Storage("")
    await storage.connect()
    paths = await svc.find_warm_paths(storage, "GitLab")
    assert paths[0]["login"] == "alice"
    assert paths[0]["warm_score"] == 0  # fixture member has no bio text
    assert paths[0]["overlap_terms"] == []


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


def test_score_candidates_ranks_by_domain_overlap():
    candidates = [
        {"login": "zed", "url": "u1", "bio": "Frontend design systems"},
        {"login": "amy", "url": "u2",
         "bio": "Data platform engineer: Python, Kafka, machine learning"},
        {"login": "bob", "url": "u3", "bio": "Python tooling"},
    ]
    ranked = svc.score_candidates(
        candidates, interest_terms=("python", "kafka", "machine learning"))
    assert [c["login"] for c in ranked] == ["amy", "bob", "zed"]
    assert ranked[0]["warm_score"] == 3
    assert set(ranked[0]["overlap_terms"]) == {"python", "kafka",
                                               "machine learning"}
    assert ranked[2]["warm_score"] == 0
    # Word-boundary: "go" must not match "algorithms".
    none = svc.score_candidates(
        [{"login": "x", "url": "u", "bio": "algorithms"}],
        interest_terms=("go",))
    assert none[0]["warm_score"] == 0


@pytest.mark.asyncio
async def test_fetch_warm_candidates_enriches_profiles(monkeypatch):
    from types import SimpleNamespace

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if "public_members" in str(request.url):
            return httpx.Response(200, json=[
                {"login": "amy", "html_url": "https://github.com/amy"},
            ])
        if str(request.url).endswith("/users/amy"):
            return httpx.Response(200, json={
                "name": "Amy A", "bio": "Kafka pipelines", "blog": "amy.dev",
            })
        return httpx.Response(404)

    real = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        svc, "httpx",
        SimpleNamespace(AsyncClient=lambda **kw: real(transport=transport, **kw)),
    )
    out = await svc._fetch_warm_candidates("Acme")
    assert out == [{"login": "amy", "url": "https://github.com/amy",
                    "source": "github_org_member", "name": "Amy A",
                    "bio": "Kafka pipelines", "blog": "amy.dev"}]
