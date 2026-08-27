# Conventions

Concrete, opinionated. Follow these mechanically — they're not preferences,
they're what keeps the codebase legible after 6 months of drift.

## Python

- **3.11+.** Use `str | None` union syntax, `dict`/`list` generics,
  `match/case` where clean.
- **`from __future__ import annotations`** at the top of every module.
  Deferred evaluation buys us cleaner circular-import handling and
  faster imports.
- **Type hints are non-optional** on every function that crosses a
  module boundary. Internal helpers may skip if the callsite makes them
  obvious.
- **Async everywhere in the hot path.** `ingestion.*`, `qualification.*`,
  `drafting.*`. Sync helpers are fine for pure computation
  (`filters.py:_parse_comp`).
- **No blocking I/O in async functions.** No `time.sleep`, no `requests`,
  no `psycopg2`. Use `asyncio.sleep`, `httpx`, `asyncpg`.

## Structure

- **One class or one concern per module.** `ashby.py` is `AshbyFetcher`
  only. `filters.py` is the pre-filter path only.
- **`__init__.py` is a re-export shim, not a place for logic.**
- **No god-modules.** If a file crosses 300 lines, look for a natural
  cut. `storage.py` is at ~350 and pushing it — next feature likely
  needs a `storage/` package.

## Pydantic

- **v2 only.** `BaseModel`, `Field(default_factory=...)`,
  `model_validate`, `model_dump`, `model_dump_json`.
- **Every cross-boundary payload is a model.** Never pass raw dicts
  between packages.
- **Prefer `Field(...)` with descriptions** on user-facing models
  (`QualificationResult`, `DraftPackage`) — LLMs read them.

## Config

- **All tunables come from env with sensible defaults.** See
  `ingestion/config.py:_env_int`.
- **No secrets in code.** Ever. `.env.example` shows the shape; real
  values go in `.env` which is `.gitignore`d.
- **Restart-required env changes are OK.** No hot-reload complexity.

## Error handling

- **LLM errors are recoverable.** Never let a bad response kill a batch.
  Downgrade to `verdict="maybe"` with an explanation.
- **Network errors are retryable.** Use `get_with_retry` in ingestion,
  the tenacity-wrapped `_post` in `openrouter.py` for LLM calls.
- **Validation errors are bugs.** `pydantic.ValidationError` from
  internal code = fix the caller. From LLM output = log + downgrade.
- **`assert` is not error handling.** Never assert on user-visible
  contracts. Use `raise ValueError(...)`.

## Logging

- **Logger per module: `log = logging.getLogger(__name__)`.**
- **Info: user-relevant events** ("fetched %s: %d jobs").
- **Warning: recoverable degradations** ("openrouter 502, retrying").
- **Exception: unexpected errors, always with `.exception(...)`** so
  the stack trace lands in the log.
- **Never `print()` in library code.** `cli.py` is the only place
  `print` is allowed — that's user-facing output.

## Async concurrency

- **Bound every `asyncio.gather` with a semaphore.** Unbounded gather =
  fastest way to get rate-limited.
- **Per-host semaphores where possible.** `ingestion/base.py`
  `_host_semaphore` is the pattern.
- **Never mutate shared state across tasks without a lock.** In
  practice, the orchestrator batches into `dict[str, Job]` after
  gather, which is fine because the mutations happen serially post-await.

## Testing

- **Deterministic. Always.** No `datetime.now()`, no network, no clock
  sleeps. If you need "now," fixture it.
- **`pytest-asyncio` in auto mode** — see `pyproject.toml`. Just
  decorate coroutines with `@pytest.mark.asyncio`.
- **Storage tests use the in-memory fallback** (`Storage("")`). Postgres
  in CI is not worth the flake.
- **LLM tests use fake clients** implementing the `QualifierClient`
  protocol or the `chat_turn` shape. See `tests/test_qualification.py`,
  `tests/test_agent.py`.
- **Parametrize regression cases.** `tests/test_filters.py` and
  `tests/test_roles.py` show the pattern.
- **New public API = new test.** No exceptions.
- **Coverage isn't the goal; behavior lock-in is.** Every guardrail in
  `CLAUDE.md §4` has a matching test.

## Prompts

- **Versioned.** `PROMPT_VERSION`, `AGENT_PROMPT_VERSION`,
  `DRAFT_PROMPT_VERSION`. Bump on material change.
- **Rendered from templates**, not concatenated inline. See
  `qualification/prompts.py`, `drafting/prompts.py`.
- **JSON-schema-terminated.** Every user prompt ends with a strict JSON
  spec the LLM must match. `QualificationResult` and `DraftPackage`
  validate the parse.
- **Voice rules go in the *system* prompt.** Task-specific data goes in
  the *user* prompt. Don't cross the streams.

## CLI

- **Subcommand-based.** `argparse.add_subparsers` with `required=True`.
- **Every command returns an int exit code from an async fn** — see
  `_run`, `_qualify`, etc. Never call `sys.exit` inside the async fn;
  return the code, let `main` do the exit.
- **Print machine-readable summaries after long ops.** `fetched=N
  inserted=N passed=N` — cron consumers appreciate it.
- **No colored output.** Terminal + log file both need to be legible.

## Storage

- **`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`** on every schema
  addition. Never a destructive migration on Kelyn's real DB.
- **JSONB for anything that would otherwise need a shape-change
  migration**: `raw`, `prefilter`, `qualification`, `stages`, `probe_results`.
- **Idempotent connect.** `storage.connect()` handles pool creation +
  SCHEMA execution. Callers never touch DDL.
- **In-memory fallback is a real code path, not a shim.** Every write
  method has an `if self.pool is None:` branch. Tests use it.

## Git & PRs

- **Small commits.** One concept per commit.
- **The commit body explains *why*.** The diff explains *what*.
- **PRs update `CLAUDE.md` when they change positioning, architecture,
  a public interface, or a rule in §4.**
- **PRs update `docs/roadmap.md` when they complete a listed item.**
- **PRs add an ADR when they reverse or supersede a documented decision.**

## What we don't do

- **No dependency injection frameworks.** Constructor args and closures
  are enough.
- **No ORM.** `asyncpg` + hand-written SQL. Storage is small enough.
- **No async context manager patterns for one-shot code paths.**
  `async with` is for actual resources (HTTP client, DB pool). Not for
  aesthetics.
- **No unnecessary abstract base classes.** `Fetcher` is one because
  the type-checker uses it, and there are 9 implementations. If there's
  only one impl, don't ABC.
- **No metaclasses.** Ever.
- **No emojis in code or docs.** Kelyn's preference; also, they render
  differently in different terminals.
