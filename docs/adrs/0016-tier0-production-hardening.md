# 0016 · Production hardening: dedup, locks, ledger, verify gate

**Status:** accepted

## Context

The 2026-08 audit found five gaps between "works" and "safe to run
unattended", and the delivery outage that followed proved the most
important one empirically: karani kept qualifying hourly while every
delivery surface was silently dark, and nothing noticed for a day.

## Decisions

**1. Cross-run canonical dedup (0.1).** The orchestrator suppresses
duplicates within a run, but the same role from an ATS and a feed
persists as two rows across runs — 12 such groups measured live. A pure
`_dedupe_canonical` helper now collapses `top_qualified` and
`autopilot_candidates` per `canonical_hash`, preferring the ATS copy
(canonical apply URL) then higher fit. Both backends share the helper so
they cannot drift. Queries over-fetch 2x so dedup still fills the limit.

**2. Advisory locks on billed runs (0.2).** The scheduler, MCP server,
and Slack listener are separate processes on one database; concurrent
`qualify`/`autopilot` passes pulled the same rows and billed twice.
`Storage.run_lock` holds a session-scoped `pg_try_advisory_lock` on a
**dedicated** connection — a pooled checkout could be returned mid-hold
and release the lock early. In-memory mode keeps a process-local set:
honest about guarding only same-process reentrancy. Contended runs
return `lock_skipped=True` rather than executing or blocking, because a
skipped hourly pass costs nothing (the next one is 60 minutes away)
while a queued one could stack.

**3. Run ledger + heartbeat (0.4).** Every pass writes a `run_ledger`
row: per-node stats, token deltas (providers now feed
`qualification.usage` counters), error count. `heartbeat_alert` reads
the newest row, and the twice-daily push prepends a warning when it is
missing or older than 3h. This closes the class of failure the outage
exposed: an in-pass error alert can never fire for a pass that never
runs. Ledger writes are best-effort — bookkeeping must not sink a hunt.

**4. Postgres-marked test suite (0.3).** Every test ran on the in-memory
fallback, so the production SQL — the UPSERT's conditional qualification
reset, the resume-hash gate, funnel FILTERs, `next_actions`, and the
advisory-lock session semantics — had zero coverage. `pytest -m pg`
runs 12 tests against a throwaway database on the compose Postgres,
excluded from default runs (the one sanctioned exception to no-network)
and wired into CircleCI with a pgvector sidecar. Each test gets a fresh
database: no shared state, no ordering dependencies, and it *skips*
(never fails) when Postgres is absent.

**5. Agent verify-before-draft (0.7).** Single-turn qualification reads
only the JD. With `[autopilot] verify = true`, the graph routes through
a `verify` node that runs agent-mode qualification per candidate before
the pack budget (up to three billed calls each) is spent; refuted
candidates are filtered by `run_autopilot(allowed_ids=...)`. This is the
graph's first conditional branch. Off by default. A verifier error
allows the candidate through — a broken verifier must degrade to
today's behavior, never silence the hunt.

## Consequences

- **Positive:** unattended operation is now bounded (locks, dedup),
  observable (ledger, heartbeat, token cost), and verifiable (real-SQL
  coverage). The verify gate spends one cheap call to protect three
  expensive ones.
- **Positive:** two latent defects surfaced while building this — a
  clock-dependent intel TTL test that rots as real time drifts from the
  fixture date, and verify tests that hit the live internet through the
  real ingest node. Both fixed; both were the kind that fail silently or
  intermittently later.
- **Negative:** the pg suite needs Docker, so most contributors will run
  a subset. Mitigated by the skip-not-fail fixture and the CI job.
- **Negative:** verify adds a per-candidate agent call when enabled.
  Bounded by the same per-run cap as drafting, and off by default.
- **Neutral:** in-memory `run_lock` cannot guarantee cross-process
  exclusion. Acceptable — that mode is for tests and scratch sessions;
  anyone running two real processes has a DSN.
