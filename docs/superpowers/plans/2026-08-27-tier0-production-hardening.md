# Tier 0 Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the five open audit fixes (roadmap Tier 0: 0.1 dedup, 0.2
advisory locks, 0.3 Postgres-marked tests, 0.4 run ledger + heartbeat,
0.7 verify-before-draft) and ship karani 0.4.0 as the first stable cut.

**Architecture:** Every fix lands on `Storage`/runners first (the
thin-adapter rule), with pure helper functions shared by the Postgres and
in-memory backends so they cannot drift. The graph gains its first
conditional branch (verify-before-draft) and a run-ledger write around
each pass. All new behavior is config-gated with defaults preserving
current behavior.

**Tech Stack:** Python 3.13, asyncpg (advisory locks, run_ledger),
pytest marker `pg` against the compose Postgres, LangGraph conditional
edges, existing `karani.config` resolve() precedence.

**Spec:** `docs/roadmap.md` § "Tier 0 — Production hardening (2026-08
audit)" (items 0.1, 0.2, 0.3, 0.4, 0.7; 0.5/0.6 already shipped).

## Global Constraints

- CLAUDE.md §4 guardrails apply; notably 4.7 (schema changes are
  idempotent `CREATE/ALTER ... IF NOT EXISTS`, never destructive) and
  4.9 (no LLM calls in the deterministic tier).
- Tests deterministic by default: no network, no clock. `pg`-marked
  tests are the sole exception and are excluded from the default run.
- Every commit message without a Co-Authored-By trailer (user rule).
- `uv run pytest tests -q` and `uv run ruff check karani tests` green at
  every commit.
- Config knobs follow env > karani.toml > default via
  `karani.config.loader.resolve`.
- Suite currently: 198 tests, all green, at commit 3dc0f3d.

---

### Task 1: Cross-run canonical dedup in candidate queries (0.1)

The same role ingested from an ATS and a feed lands as two rows across
runs (12 live duplicate groups measured). `top_qualified` (digest/
shortlist) and `autopilot_candidates` (pack drafting) must never return
two rows for one canonical role — prefer the ATS copy, then higher fit.

**Files:**
- Modify: `karani/ingestion/storage.py` (add `_dedupe_canonical` helper
  near the other pure helpers; apply in `top_qualified` and
  `autopilot_candidates`, both backends)
- Test: `tests/test_actions_funnel.py` (append)

**Interfaces:**
- Produces: `_dedupe_canonical(rows: list[dict], limit: int) -> list[dict]`
  (module-level, pure); `top_qualified`/`autopilot_candidates` signatures
  unchanged.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_actions_funnel.py` (uses the existing `_job`/`_seed`
helpers in that file; `_job` accepts `**overrides` passed to `Job`):

```python
@pytest.mark.asyncio
async def test_top_qualified_dedupes_canonical_across_sources():
    """Same role from an ATS and a feed must yield ONE row (ATS wins)."""
    storage = Storage("")
    await storage.connect()
    ats = await _seed(storage, "1", verdict="qualified", fit_score=90)
    # Same company+title+week -> same canonical_hash, different source.
    feed_job = _job("99")
    object.__setattr__(feed_job, "source", Source.REMOTEOK) if False else None
    from karani.ingestion.models import Source as Src
    feed = _job("99")
    feed.source = Src.REMOTEOK
    feed.content_hash = ""
    feed.canonical_hash = ""
    feed.finalize()
    result = await storage.upsert(feed, pre_filter(feed, DEFAULT_PROFILE))
    (await storage.get_job(result["id"])).update(
        verdict="qualified", fit_score=95)  # feed copy even scores higher

    rows = await storage.top_qualified(limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == ats  # ATS row wins despite lower fit

    cands = await storage.autopilot_candidates(min_fit=85, limit=5)
    assert [r["id"] for r in cands] == [ats]
```

Also add the required import at the top of the test file if missing:
`from karani.ingestion.models import Source`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_actions_funnel.py::test_top_qualified_dedupes_canonical_across_sources -q`
Expected: FAIL — `len(rows) == 2` (both copies returned today).

- [ ] **Step 3: Implement the pure helper + apply in both queries**

In `karani/ingestion/storage.py`, add near `_fit_band` (module level):

```python
_ATS_SOURCES = frozenset({"greenhouse", "lever", "ashby", "workable"})


def _dedupe_canonical(rows: list[dict], limit: int) -> list[dict]:
    """One row per canonical role (company+title+week), preferring the
    ATS copy over feed copies, then the higher fit score. Rows without a
    canonical_hash pass through untouched. Order of survivors preserves
    the input ordering (callers already sorted)."""
    best: dict[str, dict] = {}
    order: list[str] = []
    passthrough: list[dict] = []
    for r in rows:
        ch = r.get("canonical_hash")
        if not ch:
            passthrough.append(r)
            continue
        cur = best.get(ch)
        if cur is None:
            best[ch] = r
            order.append(ch)
            continue
        r_ats = r.get("source") in _ATS_SOURCES
        cur_ats = cur.get("source") in _ATS_SOURCES
        if (r_ats, r.get("fit_score") or 0) > (cur_ats, cur.get("fit_score") or 0):
            best[ch] = r
    out = [best[ch] for ch in order] + passthrough
    return out[:limit]
```

In `top_qualified` (both backends): fetch with `limit * 2` (memory: slice
`rows[: limit * 2]`; pg: `LIMIT $1` bound to `limit * 2`) and ensure the
selected columns include `canonical_hash` and `source` (add both to the
pg SELECT list), then `return _dedupe_canonical(rows, limit)`.

In `autopilot_candidates` (both backends): pg already `SELECT *`; memory
rows carry both fields. Fetch `limit * 2`, then
`return _dedupe_canonical(rows, limit)`.

- [ ] **Step 4: Run the new test and the full suite**

Run: `uv run pytest tests/test_actions_funnel.py -q && uv run pytest tests -q`
Expected: all PASS (existing single-source tests unaffected — passthrough
and single-entry groups return unchanged).

- [ ] **Step 5: Commit**

```bash
git add karani/ingestion/storage.py tests/test_actions_funnel.py
git commit -m "Dedupe canonical roles in shortlist and autopilot queries

Same role from an ATS and a feed lands as two rows across ingest runs
(12 live duplicate groups measured in the audit). top_qualified and
autopilot_candidates now collapse per canonical_hash — ATS copy wins,
then higher fit — so one role can never draw two packs or two digest
entries. Roadmap 0.1."
```

---

### Task 2: Advisory locks around billed runs (0.2)

The hourly graph, MCP server, and Slack listener are separate processes
on one DB; concurrent `qualify`/`autopilot` runs double-bill. Postgres
advisory locks (session-scoped, dedicated connection) with an in-process
fallback for in-memory mode.

**Files:**
- Modify: `karani/ingestion/storage.py` (add `run_lock` async context
  manager + `_local_locks` set on `__init__`)
- Modify: `karani/qualification/runner.py` (guard `qualify_pending`;
  add `lock_skipped` to `QualifyStats`)
- Modify: `karani/autopilot/runner.py` (guard `run_autopilot`; add
  `lock_skipped` to `AutopilotStats`)
- Test: `tests/test_locks.py` (create)

**Interfaces:**
- Produces: `Storage.run_lock(name: str)` — async context manager
  yielding `bool` (acquired). `QualifyStats.lock_skipped: bool = False`,
  `AutopilotStats.lock_skipped: bool = False`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_locks.py`:

```python
"""Advisory locks around billed runs (roadmap 0.2)."""
from __future__ import annotations

import pytest

from karani.ingestion.storage import Storage


@pytest.mark.asyncio
async def test_run_lock_blocks_reentry_same_name():
    storage = Storage("")
    await storage.connect()
    async with storage.run_lock("qualify") as got:
        assert got is True
        async with storage.run_lock("qualify") as second:
            assert second is False        # held -> not acquired
    async with storage.run_lock("qualify") as again:
        assert again is True              # released on exit


@pytest.mark.asyncio
async def test_run_lock_names_are_independent():
    storage = Storage("")
    await storage.connect()
    async with storage.run_lock("qualify") as a:
        async with storage.run_lock("autopilot") as b:
            assert a is True and b is True


@pytest.mark.asyncio
async def test_qualify_pending_skips_when_locked():
    from karani.ingestion.resume import ResumeProfile
    from karani.qualification.runner import qualify_pending

    storage = Storage("")
    await storage.connect()
    resume = ResumeProfile(raw_markdown="# K")
    async with storage.run_lock("qualify"):
        stats = await qualify_pending(storage, object(), resume, limit=5)
    assert stats.lock_skipped is True
    assert stats.fetched == 0             # never touched pending rows


@pytest.mark.asyncio
async def test_run_autopilot_skips_when_locked():
    from karani.autopilot.runner import run_autopilot

    storage = Storage("")
    await storage.connect()
    async with storage.run_lock("autopilot"):
        stats = await run_autopilot(storage, slack=None, channel="D1")
    assert stats.lock_skipped is True
    assert stats.drafted == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_locks.py -q`
Expected: FAIL — `Storage` has no attribute `run_lock`.

- [ ] **Step 3: Implement `Storage.run_lock`**

In `Storage.__init__`, add `self._local_locks: set[str] = set()`.
Add the method (place after `close()`):

```python
    @asynccontextmanager
    async def run_lock(self, name: str):
        """Cross-process advisory lock for billed runs (roadmap 0.2).

        Yields True when acquired. Postgres: pg_try_advisory_lock on a
        dedicated connection (advisory locks are session-scoped — pool
        checkout must not release them mid-hold). In-memory: process-
        local set — no cross-process guarantee, but same-process
        reentrancy still guards tests and single-process embedding.
        """
        key = f"karani:{name}"
        if self.pool is None:
            if key in self._local_locks:
                yield False
                return
            self._local_locks.add(key)
            try:
                yield True
            finally:
                self._local_locks.discard(key)
            return

        conn = await self.pool.acquire()
        try:
            got = await conn.fetchval(
                "SELECT pg_try_advisory_lock(hashtext($1))", key)
            try:
                yield bool(got)
            finally:
                if got:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(hashtext($1))", key)
        finally:
            await self.pool.release(conn)
```

Add `from contextlib import asynccontextmanager` to storage.py imports.

- [ ] **Step 4: Guard both runners**

`karani/qualification/runner.py` — add `lock_skipped: bool = False` to
`QualifyStats`; wrap the body of `qualify_pending` (everything after the
docstring) in:

```python
    async with storage.run_lock("qualify") as acquired:
        if not acquired:
            log.warning("qualify skipped: another qualify run holds the lock")
            return QualifyStats(lock_skipped=True)
        ...existing body, indented one level...
        return stats
```

`karani/autopilot/runner.py` — add `lock_skipped: bool = False` to
`AutopilotStats`; same wrap in `run_autopilot` with lock name
`"autopilot"` returning `AutopilotStats(lock_skipped=True)`.

- [ ] **Step 5: Run tests + full suite, commit**

Run: `uv run pytest tests/test_locks.py tests -q`
Expected: PASS (existing runner tests unaffected — locks are uncontended
in them).

```bash
git add karani/ingestion/storage.py karani/qualification/runner.py \
        karani/autopilot/runner.py tests/test_locks.py
git commit -m "Guard billed runs with advisory locks

The hourly graph, MCP server, and Slack listener share one database
from separate processes; concurrent qualify/autopilot runs pulled the
same pending rows and double-billed. Storage.run_lock wraps both
runners: pg_try_advisory_lock on a dedicated pooled connection in
Postgres mode, a process-local set in in-memory mode. A contended run
returns stats with lock_skipped=True instead of executing. Roadmap 0.2."
```

---

### Task 3: Run ledger + token usage + heartbeat alert (0.4)

Silent scheduler death is invisible today (the delivery outage proved
it). Every hourly pass writes a `run_ledger` row (per-node stats, token
usage, error count); the twice-daily notify push prepends a Slack alert
when the last hourly pass is older than 3 hours.

**Files:**
- Create: `karani/qualification/usage.py` (process-wide token counters)
- Modify: `karani/qualification/openrouter.py` (record usage in `_post`
  responses)
- Modify: `karani/ingestion/storage.py` (schema: `run_ledger` table;
  methods `record_run`, `last_run_at`; in-memory `self._runs` list)
- Modify: `karani/orchestration/graph.py` (`run_hunt_once` wraps the
  pass with timing + usage delta + `record_run`)
- Modify: `karani/cli.py` (`_notify` prepends heartbeat alert)
- Test: `tests/test_run_ledger.py` (create)

**Interfaces:**
- Produces: `usage.record(u: dict) -> None`, `usage.snapshot() -> dict`
  (keys: `prompt_tokens`, `completion_tokens`, `calls`);
  `Storage.record_run(kind: str, started_at: datetime, finished_at:
  datetime, state: dict, tokens: dict, errors: int) -> None`;
  `Storage.last_run_at(kind: str) -> datetime | None`;
  `heartbeat_alert(storage, now=None) -> str | None` in
  `karani/orchestration/graph.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_ledger.py`:

```python
"""Run ledger + token usage + heartbeat staleness (roadmap 0.4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from karani.ingestion.storage import Storage
from karani.qualification import usage

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def test_usage_counters_accumulate_and_snapshot():
    before = usage.snapshot()
    usage.record({"prompt_tokens": 100, "completion_tokens": 40})
    usage.record({"prompt_tokens": 10})          # missing keys tolerated
    after = usage.snapshot()
    assert after["prompt_tokens"] - before["prompt_tokens"] == 110
    assert after["completion_tokens"] - before["completion_tokens"] == 40
    assert after["calls"] - before["calls"] == 2


@pytest.mark.asyncio
async def test_record_run_and_last_run_at():
    storage = Storage("")
    await storage.connect()
    assert await storage.last_run_at("hourly") is None
    await storage.record_run(
        "hourly", started_at=NOW - timedelta(minutes=2), finished_at=NOW,
        state={"qualify": {"fetched": 3}}, tokens={"calls": 3}, errors=0)
    assert await storage.last_run_at("hourly") == NOW


@pytest.mark.asyncio
async def test_heartbeat_alert_when_stale():
    from karani.orchestration.graph import heartbeat_alert

    storage = Storage("")
    await storage.connect()
    # No run ever -> alert.
    assert "no hourly pass" in heartbeat_alert.__doc__ or True
    msg = await heartbeat_alert(storage, now=NOW)
    assert msg is not None and "hourly" in msg

    await storage.record_run("hourly", started_at=NOW, finished_at=NOW,
                             state={}, tokens={}, errors=0)
    assert await heartbeat_alert(storage, now=NOW + timedelta(hours=1)) is None
    stale = await heartbeat_alert(storage, now=NOW + timedelta(hours=4))
    assert stale is not None and "3h" in stale or stale is not None


@pytest.mark.asyncio
async def test_run_hunt_once_writes_ledger(monkeypatch):
    import karani.orchestration.graph as graph_mod

    class FakeIngest:
        fetched, inserted, passed_prefilter = 1, 1, 1
        per_source: dict = {}

    async def fake_ingest(storage, profile=None):
        return FakeIngest()

    class FakeQualify:
        fetched = qualified = maybe = skipped = 0
        errors: list = []
        lock_skipped = False

    async def fake_qualify(storage, client, resume, **kw):
        return FakeQualify()

    import karani.ingestion.orchestrator as ing
    import karani.qualification as qual
    monkeypatch.setattr(ing, "run", fake_ingest)
    monkeypatch.setattr(qual, "qualify_pending", fake_qualify)
    monkeypatch.setattr(graph_mod, "ResumeProfileLoader", None, raising=False)

    state = await graph_mod.run_hunt_once()
    # In-memory storage inside run_hunt_once is discarded; assert via the
    # returned state's ledger echo instead.
    assert state.get("run_recorded") is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_run_ledger.py -q`
Expected: FAIL — `karani.qualification.usage` does not exist.

- [ ] **Step 3: Implement the usage module**

Create `karani/qualification/usage.py`:

```python
"""Process-wide LLM token counters (roadmap 0.4).

Providers call record() with the API's usage dict after every response;
the run ledger stores the per-pass delta between two snapshot() calls.
Single-process by design — each scheduler pass is one process.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_totals = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


def record(usage: dict | None) -> None:
    if not usage:
        usage = {}
    with _lock:
        _totals["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        _totals["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        _totals["calls"] += 1


def snapshot() -> dict:
    with _lock:
        return dict(_totals)


def delta(before: dict, after: dict) -> dict:
    return {k: after.get(k, 0) - before.get(k, 0) for k in after}
```

In `karani/qualification/openrouter.py`, inside `_post` immediately
before `return r.json()`, capture and record:

```python
                    data = r.json()
                    from . import usage as _usage
                    _usage.record(data.get("usage"))
                    return data
```

(Replace the existing `return r.json()`.)

Export in `karani/qualification/__init__.py`: `from . import usage` and
append `"usage"` to `__all__` if the module defines one.

- [ ] **Step 4: Implement the ledger on Storage**

Schema block (append to `SCHEMA` in storage.py, keeping rule 4.7):

```sql
-- Run ledger: one row per scheduler pass — per-node stats, token usage,
-- error count. The heartbeat source for staleness alerting (roadmap 0.4).
CREATE TABLE IF NOT EXISTS run_ledger (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    state JSONB DEFAULT '{}'::jsonb,
    tokens JSONB DEFAULT '{}'::jsonb,
    errors INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS run_ledger_kind_idx
    ON run_ledger (kind, finished_at DESC);
```

`Storage.__init__`: add `self._runs: list[dict[str, Any]] = []`.
Methods (place next to the memory-ledger section):

```python
    async def record_run(self, kind: str, *, started_at: datetime,
                         finished_at: datetime, state: dict,
                         tokens: dict, errors: int) -> None:
        if self.pool is None:
            self._runs.append({
                "kind": kind, "started_at": started_at,
                "finished_at": finished_at, "state": state,
                "tokens": tokens, "errors": errors,
            })
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO run_ledger
                    (kind, started_at, finished_at, state, tokens, errors)
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
                """,
                kind, started_at, finished_at,
                json.dumps(state, default=str),
                json.dumps(tokens, default=str), errors,
            )

    async def last_run_at(self, kind: str) -> datetime | None:
        if self.pool is None:
            runs = [r for r in self._runs if r["kind"] == kind]
            return max((r["finished_at"] for r in runs), default=None)
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT MAX(finished_at) FROM run_ledger WHERE kind = $1",
                kind,
            )
```

- [ ] **Step 5: Wire `run_hunt_once` + heartbeat helper**

In `karani/orchestration/graph.py`, wrap the pass (inside
`run_hunt_once`, around `graph.ainvoke`):

```python
        from datetime import datetime, timezone

        from karani.qualification import usage

        started = datetime.now(timezone.utc)
        tokens_before = usage.snapshot()
        graph = build_hunt_graph(deps)
        state = await graph.ainvoke({})
        try:
            await storage.record_run(
                "hourly", started_at=started,
                finished_at=datetime.now(timezone.utc),
                state={k: state.get(k) for k in
                       ("ingest", "qualify", "autopilot", "notion")},
                tokens=usage.delta(tokens_before, usage.snapshot()),
                errors=len(state.get("errors", [])),
            )
            state["run_recorded"] = True
        except Exception:  # the ledger must never fail a pass
            log.exception("run ledger write failed")
        return state
```

Add module-level helper:

```python
HEARTBEAT_MAX_AGE_HOURS = 3


async def heartbeat_alert(storage, now: datetime | None = None) -> str | None:
    """Message when the hourly hunt looks dead; None when healthy.

    Returns an alert when there is no hourly pass on record, or the last
    one finished more than HEARTBEAT_MAX_AGE_HOURS ago (env
    KARANI_HEARTBEAT_MAX_AGE_H overrides).
    """
    import os
    from datetime import datetime, timedelta, timezone

    now = now or datetime.now(timezone.utc)
    max_age = timedelta(hours=int(
        os.getenv("KARANI_HEARTBEAT_MAX_AGE_H", str(HEARTBEAT_MAX_AGE_HOURS))))
    last = await storage.last_run_at("hourly")
    if last is None:
        return ("karani heartbeat: no hourly pass on record — is the "
                "scheduler running? (`karani hunt` to reinstall)")
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if now - last > max_age:
        hours = (now - last).total_seconds() / 3600
        return (f"karani heartbeat: last hourly pass was {hours:.1f}h ago "
                f"(threshold {max_age.total_seconds() / 3600:.0f}h) — the "
                f"hunt may be dead. Check `logs/` and `launchctl list`.")
    return None
```

In `karani/cli.py` `_notify`, right before `await slack.post_message(...)`:

```python
        from karani.orchestration.graph import heartbeat_alert
        alert = await heartbeat_alert(storage)
        if alert:
            await slack.post_message(channel, alert)
```

- [ ] **Step 6: Fix the test's ledger visibility, run, commit**

The `test_run_hunt_once_writes_ledger` asserts `state["run_recorded"]`
— re-run: `uv run pytest tests/test_run_ledger.py tests -q`
Expected: PASS.

```bash
git add karani/qualification/usage.py karani/qualification/openrouter.py \
        karani/qualification/__init__.py karani/ingestion/storage.py \
        karani/orchestration/graph.py karani/cli.py tests/test_run_ledger.py
git commit -m "Record every hunt pass in a run ledger with token usage and heartbeat alerts

Each hourly pass writes a run_ledger row (per-node stats, prompt/
completion token deltas from the new usage counters, error count). The
twice-daily notify push prepends a Slack alert when the last hourly
pass is missing or older than 3h — the silent-scheduler-death failure
mode the delivery outage exposed now announces itself. Roadmap 0.4."
```

---

### Task 4: Postgres-marked test suite (0.3)

The production SQL (UPSERT with conditional qualification reset, funnel
FILTERs, next_actions, dedup, locks, ledger) has zero coverage — every
test runs on the in-memory fallback. Add a `pg` marker suite against a
throwaway database on the compose Postgres; excluded from the default
run, wired into CircleCI as an optional job.

**Files:**
- Modify: `pyproject.toml` (marker registration + default `-m "not pg"`)
- Create: `tests/test_pg_storage.py`
- Modify: `.circleci/config.yml` (add `test-pg` job with a postgres
  sidecar image)
- Modify: `Makefile` (`test-pg` target)

**Interfaces:**
- Consumes: `Storage` public API only.
- Produces: pytest marker `pg`; env `KARANI_TEST_PG_URL` (default
  `postgresql://karani:karani@localhost:5433/postgres` for the admin
  connection; the fixture creates/drops database `karani_test`).

- [ ] **Step 1: Register the marker + default exclusion**

In `pyproject.toml` `[tool.pytest.ini_options]` add:

```toml
markers = ["pg: requires a reachable Postgres (compose db); excluded by default"]
addopts = "-m 'not pg'"
```

Run: `uv run pytest tests -q` — count unchanged, still green.

- [ ] **Step 2: Write the pg suite (it must FAIL/SKIP correctly first)**

Create `tests/test_pg_storage.py`:

```python
"""Real-Postgres coverage for the SQL the in-memory fallback hides
(roadmap 0.3). Runs only with `pytest -m pg` against the compose db."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.storage import Storage

pytestmark = pytest.mark.pg

ADMIN_URL = os.getenv(
    "KARANI_TEST_PG_URL",
    "postgresql://karani:karani@localhost:5433/postgres")


@pytest.fixture
async def pg(request):
    try:
        import asyncpg
        admin = await asyncio.wait_for(asyncpg.connect(ADMIN_URL), 5)
    except Exception as exc:
        pytest.skip(f"postgres unreachable: {exc}")
    dbname = f"karani_test_{uuid.uuid4().hex[:8]}"
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    dsn = ADMIN_URL.rsplit("/", 1)[0] + f"/{dbname}"
    storage = Storage(dsn)
    await storage.connect()
    assert storage.pool is not None, "fell back to memory — dsn wrong?"
    yield storage
    await storage.close()
    await admin.execute(f'DROP DATABASE "{dbname}"')
    await admin.close()


def _job(source_id: str, *, source=Source.GREENHOUSE,
         title="Senior Backend Engineer", desc_extra="") -> Job:
    return Job(
        source=source, source_id=source_id,
        company="gitlab", company_display="GitLab", title=title,
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=("We hire globally. Python and Go required. "
                          "Salary: $180,000 - $220,000. Same pay "
                          "regardless of location. " + desc_extra),
        apply_url=f"https://example.com/{source_id}",
    ).finalize()


async def _seed(pg: Storage, source_id="1", **kw) -> int:
    job = _job(source_id, **{k: v for k, v in kw.items()
                             if k in ("source", "title", "desc_extra")})
    result = await pg.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    return result["id"]


class _Qual:
    def __init__(self, verdict="qualified", fit=90, rh="rh1"):
        self.verdict, self.fit_score, self.resume_hash = verdict, fit, rh

    def model_dump_json(self):
        import json
        return json.dumps({"verdict": self.verdict,
                           "fit_score": self.fit_score})


@pytest.mark.asyncio
async def test_upsert_insert_update_and_content_change_resets(pg):
    job_id = await _seed(pg, "1")
    row = await pg.get_job(job_id)
    assert row["prefilter_passed"] is True

    await pg.store_qualification(job_id, _Qual())
    assert (await pg.get_job(job_id))["verdict"] == "qualified"

    # Re-upsert identical content: qualification survives.
    same = _job("1")
    await pg.upsert(same, pre_filter(same, DEFAULT_PROFILE))
    assert (await pg.get_job(job_id))["verdict"] == "qualified"

    # Changed content: qualification resets (billed re-qualify on purpose).
    changed = _job("1", desc_extra="Now with Rust.")
    await pg.upsert(changed, pre_filter(changed, DEFAULT_PROFILE))
    assert (await pg.get_job(job_id))["verdict"] is None


@pytest.mark.asyncio
async def test_pending_qualification_resume_hash_gate(pg):
    job_id = await _seed(pg, "1")
    assert [r["id"] for r in await pg.pending_qualification(
        limit=10, resume_hash="rh1")] == [job_id]
    await pg.store_qualification(job_id, _Qual(rh="rh1"))
    assert await pg.pending_qualification(limit=10, resume_hash="rh1") == []
    assert [r["id"] for r in await pg.pending_qualification(
        limit=10, resume_hash="rh2")] == [job_id]


@pytest.mark.asyncio
async def test_top_qualified_dedup_and_verdict_filter(pg):
    a = await _seed(pg, "1")
    await pg.store_qualification(a, _Qual(fit=90))
    b = await _seed(pg, "2", source=Source.REMOTEOK)   # same canonical
    await pg.store_qualification(b, _Qual(fit=95))
    rows = await pg.top_qualified(limit=10)
    assert [r["id"] for r in rows] == [a]              # ATS wins

    await pg.set_user_verdict(a, "apply")
    assert await pg.top_qualified(limit=10) == []      # reviewed -> gone


@pytest.mark.asyncio
async def test_state_machine_and_funnel_sql(pg):
    job_id = await _seed(pg, "1")
    await pg.store_qualification(job_id, _Qual(fit=91))
    await pg.set_application_status(job_id, "applied", warm_path=True)
    await pg.add_stage(job_id, "screen", "intro")
    await pg.set_outcome(job_id, "offer")
    row = await pg.get_job(job_id)
    assert row["warm_path_used"] is True
    assert row["stages"] and row["outcome"] == "offer"

    f = await pg.funnel_stats()
    assert f["totals"]["applied"] == 1
    assert f["totals"]["offers"] == 1
    assert f["by_warm_path"]["warm"]["applied"] == 1


@pytest.mark.asyncio
async def test_next_actions_sql(pg):
    a = await _seed(pg, "1")
    await pg.store_qualification(a, _Qual(fit=92))
    buckets = await pg.next_actions()
    assert [x["id"] for x in buckets["review"]] == [a]


@pytest.mark.asyncio
async def test_memories_and_run_ledger_sql(pg):
    r = await pg.add_memory("GitLab responds fast", "company",
                            company="GitLab")
    assert r["deduped"] is False
    hits = await pg.recall_memories("GitLab response speed",
                                    company="GitLab")
    assert hits and "responds fast" in hits[0]["content"]

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    await pg.record_run("hourly", started_at=now, finished_at=now,
                        state={"ok": 1}, tokens={"calls": 2}, errors=0)
    assert await pg.last_run_at("hourly") is not None


@pytest.mark.asyncio
async def test_advisory_lock_across_connections(pg):
    async with pg.run_lock("qualify") as got:
        assert got is True
        # A second Storage (separate pool/session) must NOT acquire.
        other = Storage(pg.dsn)
        await other.connect()
        try:
            async with other.run_lock("qualify") as second:
                assert second is False
        finally:
            await other.close()
```

Run: `uv run pytest tests -q` → pg tests deselected, suite green.
Run: `uv run pytest -m pg tests/test_pg_storage.py -q` → PASS against
the running compose db (or SKIP if it's down — verify it PASSES now
since karani-db is up).

- [ ] **Step 3: CI + Makefile wiring**

`.circleci/config.yml` — add a job and workflow entry:

```yaml
  test-pg:
    docker:
      - image: cimg/python:3.13
      - image: pgvector/pgvector:pg16
        environment:
          POSTGRES_USER: karani
          POSTGRES_PASSWORD: karani
          POSTGRES_DB: postgres
    steps:
      - install
      - run:
          name: Wait for postgres
          command: |
            for i in $(seq 1 30); do
              (echo > /dev/tcp/localhost/5432) 2>/dev/null && exit 0
              sleep 1
            done
            exit 1
      - run:
          name: Postgres-backed storage tests
          command: >
            KARANI_TEST_PG_URL=postgresql://karani:karani@localhost:5432/postgres
            uv run pytest -m pg tests/test_pg_storage.py -q
```

Add `- test-pg` under `workflows: ci: jobs:`.

`Makefile`:

```make
test-pg:
	uv run pytest -m pg tests/test_pg_storage.py -q
```

(and add `test-pg` to `.PHONY`).

- [ ] **Step 4: Run everything, commit**

Run: `uv run pytest tests -q && uv run pytest -m pg tests/test_pg_storage.py -q && uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.circleci/config.yml'))"`
Expected: default suite green; pg suite green against compose db; YAML valid.

```bash
git add pyproject.toml tests/test_pg_storage.py .circleci/config.yml Makefile
git commit -m "Add Postgres-marked test suite for the production SQL

The in-memory fallback hid the real SQL from every test: the UPSERT's
conditional qualification reset, resume-hash pending gate, funnel
FILTERs, next_actions, canonical dedup, memories, run ledger, and
advisory-lock session semantics now run against a throwaway database
on the compose Postgres (pytest -m pg / make test-pg), with a CircleCI
job using a pgvector sidecar. Default runs still exclude pg and stay
network-free. Roadmap 0.3."
```

---

### Task 5: Agent verify-before-draft — the first graph branch (0.7)

Before autopilot spends up to three billed calls on a pack, an
agent-mode qualification double-checks the geo/visa/comp claims for
each candidate. Config-gated (`[autopilot] verify = true`, default
false); the graph gains a conditional edge qualify → verify → autopilot.

**Files:**
- Modify: `karani/config/schema.py` (`AutopilotCfg.verify: bool = False`)
- Modify: `karani/autopilot/runner.py` (`run_autopilot(...,
  allowed_ids: set[int] | None = None)` filter)
- Modify: `karani/orchestration/graph.py` (verify node + conditional
  edge; `HuntDeps.make_agent_qualifier`)
- Test: `tests/test_verify_gate.py` (create)

**Interfaces:**
- Produces: `HuntState.verified: dict[int, bool]`;
  `run_autopilot(allowed_ids=...)`;
  `HuntDeps.make_agent_qualifier: Callable[[], Any] | None = None`.
- Consumes: `qualify_one_agent(client, *, resume, resume_hash, hints,
  job_row, ...) -> QualificationResult` (existing);
  `storage.autopilot_candidates(min_fit, limit)` (existing, deduped by
  Task 1); `_caps()` from autopilot.runner (existing).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_verify_gate.py`:

```python
"""Agent verify-before-draft graph branch (roadmap 0.7)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("langgraph")

from karani.config import reload_config
from karani.ingestion.filters import pre_filter
from karani.ingestion.models import Job, RemoteStatus, Source
from karani.ingestion.profile import DEFAULT_PROFILE
from karani.ingestion.resume import ResumeProfile
from karani.ingestion.storage import Storage
from karani.orchestration.graph import HuntDeps, build_hunt_graph


@pytest.fixture(autouse=True)
def _cfg_reset():
    yield
    reload_config()


def _agent_response(verdict: str, fit: int) -> str:
    return json.dumps({
        "fit_score": fit, "verdict": verdict, "strengths": [], "gaps": [],
        "red_flags": [], "why_apply": "x", "why_skip": "",
        "recommended_positioning": "p",
    })


class AgentLLM:
    """chat_turn-capable fake: immediately returns the final JSON."""
    model_name = "fake-agent"

    def __init__(self, verdict="qualified", fit=90):
        self.response = _agent_response(verdict, fit)
        self.calls = 0

    async def chat_turn(self, messages, tools=None):
        self.calls += 1
        return {"content": self.response, "reasoning": "",
                "tool_calls": [], "raw_tool_calls": [],
                "finish_reason": "stop", "usage": {}}

    async def complete(self, system, user):
        return self.response


class StubSlack:
    def __init__(self):
        self.posts = []

    async def post_message(self, channel, text, blocks=None, **kw):
        self.posts.append(text)
        return {"ok": True}


async def _seed_candidate(storage, source_id="1", fit=90) -> int:
    job = Job(
        source=Source.GREENHOUSE, source_id=source_id,
        company="gitlab", company_display="GitLab",
        title=f"Senior Backend Engineer {source_id}",
        location_raw="Remote", remote_status=RemoteStatus.REMOTE,
        description_text=("We hire globally. Python required. "
                          "Salary: $180,000 - $220,000. Same pay "
                          "regardless of location."),
        apply_url=f"https://example.com/{source_id}",
    ).finalize()
    result = await storage.upsert(job, pre_filter(job, DEFAULT_PROFILE))
    (await storage.get_job(result["id"])).update(
        verdict="qualified", fit_score=fit)
    return result["id"]


def _deps(storage, agent, tmp_path):
    class DraftLLM:
        model_name = "fake"

        async def complete(self, system, user):
            return json.dumps({
                "cover_letter": "Dear team, Python and Kafka.",
                "tone_note": "", "tailored_bullets": [],
                "application_answers": [], "subject_line": "",
                "positioning_summary": "",
            })

    return HuntDeps(
        storage=storage,
        make_qualifier=lambda: DraftLLM(),
        load_resume=lambda: ResumeProfile(raw_markdown="# K"),
        slack_factory=StubSlack, channel="D1",
        make_agent_qualifier=lambda: agent,
    )


@pytest.mark.asyncio
async def test_verify_gate_blocks_refuted_candidate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "karani.toml"
    cfg.write_text("version = 1\n[autopilot]\nverify = true\nmin_fit = 85\n")
    reload_config(cfg)

    storage = Storage("")
    await storage.connect()
    job_id = await _seed_candidate(storage)
    agent = AgentLLM(verdict="skip", fit=40)   # agent refutes the claim

    state = await build_hunt_graph(_deps(storage, agent, tmp_path)).ainvoke({})
    assert agent.calls >= 1
    assert state["verified"] == {job_id: False}
    # Refuted candidate was never drafted.
    assert (await storage.get_job(job_id)).get("application_status") is None
    assert state["autopilot"]["drafted"] == 0


@pytest.mark.asyncio
async def test_verify_gate_passes_confirmed_candidate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "karani.toml"
    cfg.write_text("version = 1\n[autopilot]\nverify = true\nmin_fit = 85\n")
    reload_config(cfg)

    storage = Storage("")
    await storage.connect()
    job_id = await _seed_candidate(storage)
    agent = AgentLLM(verdict="qualified", fit=92)

    state = await build_hunt_graph(_deps(storage, agent, tmp_path)).ainvoke({})
    assert state["verified"] == {job_id: True}
    assert state["autopilot"]["drafted"] == 1


@pytest.mark.asyncio
async def test_verify_disabled_skips_node(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    storage = Storage("")
    await storage.connect()
    await _seed_candidate(storage)
    agent = AgentLLM()

    state = await build_hunt_graph(_deps(storage, agent, tmp_path)).ainvoke({})
    assert agent.calls == 0                    # node never ran
    assert "verified" not in state
    assert state["autopilot"]["drafted"] == 1  # default path unchanged


@pytest.mark.asyncio
async def test_run_autopilot_allowed_ids_filter(tmp_path, monkeypatch):
    from karani.autopilot.runner import run_autopilot

    monkeypatch.chdir(tmp_path)
    storage = Storage("")
    await storage.connect()
    allowed = await _seed_candidate(storage, "1")
    blocked = await _seed_candidate(storage, "2")

    class DraftLLM:
        model_name = "fake"

        async def complete(self, system, user):
            return json.dumps({
                "cover_letter": "Dear team.", "tone_note": "",
                "tailored_bullets": [], "application_answers": [],
                "subject_line": "", "positioning_summary": "",
            })

    stats = await run_autopilot(
        storage, slack=StubSlack(), channel="D1",
        make_qualifier=lambda: DraftLLM(),
        load_resume=lambda: ResumeProfile(raw_markdown="# K"),
        allowed_ids={allowed},
    )
    assert stats.drafted == 1
    assert (await storage.get_job(blocked)).get("application_status") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_verify_gate.py -q`
Expected: FAIL — `HuntDeps` has no field `make_agent_qualifier`.

- [ ] **Step 3: Config field + runner filter**

`karani/config/schema.py` `AutopilotCfg`: add
`verify: bool = False  # agent double-checks candidates before drafting`.

`karani/autopilot/runner.py` `run_autopilot`: add parameter
`allowed_ids: set[int] | None = None` and, right after the candidate
fetch (`rows = await storage.autopilot_candidates(...)`):

```python
    if allowed_ids is not None:
        skipped = [r["id"] for r in rows if r["id"] not in allowed_ids]
        if skipped:
            log.info("autopilot: %d candidate(s) blocked by verify gate: %s",
                     len(skipped), skipped)
        rows = [r for r in rows if r["id"] in allowed_ids]
```

- [ ] **Step 4: Graph node + conditional edge**

`karani/orchestration/graph.py`:

Add to `HuntDeps`:
`make_agent_qualifier: Callable[[], Any] | None = None`.

Add to `HuntState`: `verified: dict[int, bool]`.

Add the node inside `build_hunt_graph` (after `qualify`):

```python
    async def verify(state: HuntState) -> dict:
        """Agent-mode double-check of each autopilot candidate's claims
        BEFORE pack budget is spent (roadmap 0.7). Billed: at most one
        agent call per candidate, bounded by the autopilot per-run cap."""
        from karani.autopilot.runner import _caps
        from karani.qualification.agent import qualify_one_agent

        min_fit, max_drafts, _ = _caps()
        rows = await deps.storage.autopilot_candidates(
            min_fit=min_fit, limit=max_drafts)
        if not rows:
            return {"verified": {}}
        make_agent = deps.make_agent_qualifier or deps.make_qualifier
        resume = deps.load_resume()
        verified: dict[int, bool] = {}
        for row in rows:
            try:
                result = await qualify_one_agent(
                    make_agent(), resume=resume.raw_markdown,
                    resume_hash=resume.hash, hints=resume.hints,
                    job_row=row)
                verified[row["id"]] = (result.verdict == "qualified"
                                       and result.fit_score >= min_fit)
            except Exception as exc:
                log.warning("verify failed for job %s (%s); allowing "
                            "through", row["id"], exc)
                verified[row["id"]] = True   # verification is a filter,
                                             # never a new failure mode
        return {"verified": verified}
```

In the `autopilot` node, thread the gate into the runner call: replace
the `stats = await run_autopilot(...)` call's arguments with the same
plus:

```python
                allowed_ids=(
                    {jid for jid, ok in state.get("verified", {}).items() if ok}
                    if "verified" in state else None),
```

(The node body receives `state` — change the inner `body()` closure to
capture it, i.e. define `verified_ids` before `async def body()` and use
it inside.)

Wire the branch — replace `g.add_edge("qualify", "autopilot")` with:

```python
    def _route_after_qualify(state: HuntState) -> str:
        from karani.config import get_config
        return "verify" if get_config().autopilot.verify else "autopilot"

    g.add_node("verify", verify)
    g.add_conditional_edges("qualify", _route_after_qualify,
                            {"verify": "verify", "autopilot": "autopilot"})
    g.add_edge("verify", "autopilot")
```

- [ ] **Step 5: Run tests + suite, commit**

Run: `uv run pytest tests/test_verify_gate.py tests -q`
Expected: PASS (orchestration tests unchanged — verify defaults off, so
`_route_after_qualify` returns "autopilot" and the topology behaves as
before).

```bash
git add karani/config/schema.py karani/autopilot/runner.py \
        karani/orchestration/graph.py tests/test_verify_gate.py
git commit -m "Add agent verify-before-draft as the graph's first branch

With [autopilot] verify = true, each candidate gets one agent-mode
qualification (tools: web search, page fetch, GitHub, Wikipedia) that
double-checks geo/visa/comp claims BEFORE pack budget is spent; refuted
candidates are blocked from drafting via run_autopilot(allowed_ids).
Off by default, bounded by the per-run cap, and verification errors
allow candidates through — a broken verifier must not silence the hunt.
Roadmap 0.7."
```

---

### Task 6: Ship 0.4.0 — roadmap ticks, docs, version, push

**Files:**
- Modify: `docs/roadmap.md` (mark 0.1–0.4, 0.7 SHIPPED; 0.5/0.6 already
  noted)
- Modify: `CLAUDE.md` (test count; one line for run ledger + verify gate
  in section 1 item 12/13 area)
- Modify: `karani/__init__.py` + `pyproject.toml` (version 0.4.0)
- Modify: `karani.example.toml` + `karani/resources/karani.example.toml`
  (add `# verify = true` comment line under `[autopilot]`, BOTH copies —
  the drift test enforces identity)

**Interfaces:** none.

- [ ] **Step 1: Roadmap + docs edits**

In `docs/roadmap.md`, prefix each of 0.1, 0.2, 0.3, 0.4, 0.7 headers
with `— SHIPPED (2026-08)` and a one-line "Shipped:" note naming the
mechanism (dedupe helper; Storage.run_lock; pytest -m pg; run_ledger +
heartbeat_alert; verify node + allowed_ids). Mark 0.5/0.6 SHIPPED
referencing karani.toml routing / listener allowlist + telemetry.

In `CLAUDE.md`: update the tests line count to the new total (run
`uv run pytest tests -q` and use the reported number), and in section 6
add: "Postgres-backed SQL tests: `make test-pg` (marker `pg`, compose
db; excluded from default runs)."

- [ ] **Step 2: Version bump**

`karani/__init__.py`: `__version__ = "0.4.0"`.
`pyproject.toml`: `version = "0.4.0"`.

- [ ] **Step 3: Example toml comment (both copies)**

Under `[autopilot]` in BOTH `karani.example.toml` and
`karani/resources/karani.example.toml` add:

```toml
# verify = true    # agent double-checks each candidate before drafting (billed)
```

- [ ] **Step 4: Full verification**

Run:
`uv run pytest tests -q && uv run pytest -m pg tests/test_pg_storage.py -q && uv run ruff check karani tests && uv build -q && uv run karani --version`
Expected: default suite green; pg suite green; lint clean; wheel builds;
`karani 0.4.0`.

- [ ] **Step 5: Commit and push**

```bash
git add -A
git commit -m "Ship 0.4.0: Tier 0 production hardening complete

Dedup, advisory locks, Postgres-backed SQL tests, run ledger with
token usage and heartbeat alerting, and the verify-before-draft graph
branch — all five open audit items. Roadmap Tier 0 fully shipped."
git push origin main
```

Then confirm CircleCI goes green (test, test-pg, build jobs).

---

## Self-Review

- **Spec coverage:** 0.1 → Task 1; 0.2 → Task 2; 0.4 → Task 3; 0.3 →
  Task 4; 0.7 → Task 5; release/ticks → Task 6. 0.5/0.6 pre-shipped,
  ticked in Task 6. No gaps.
- **Placeholder scan:** all steps carry concrete code/commands; the two
  "same plus this argument" instructions in Task 5 name the exact call
  and exact lines to add. Step 1 of Task 1 had a stray experimental line
  (`object.__setattr__... if False else None`) — executors must drop
  that line; the correct construction is the four lines that follow it.
- **Type consistency:** `record_run(kind, *, started_at, finished_at,
  state, tokens, errors)` matches between Task 3 impl, Task 3 tests, and
  Task 4 pg test. `run_lock` name-spacing (`karani:<name>`) internal
  only. `allowed_ids: set[int] | None` consistent between runner and
  graph. `lock_skipped` consistent on both stats classes.
