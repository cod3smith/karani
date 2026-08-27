# Architecture

## Data flow (top-to-bottom)

```
┌────────────────────────────────────────────────────────────────────┐
│  Ingestion sources (9)                                             │
│    ATS per-company:  Greenhouse · Lever · Ashby · Workable         │
│    Feed:             RemoteOK · Himalayas · WWR · Remotive · aijobs│
└────────────────────────────────────────────────────────────────────┘
                                │
                    async fetch + tenacity retry
                    per-host semaphore (concurrency=3)
                                │
                                ▼
                     ┌──────────────────────┐
                     │  Job (pydantic)      │  content_hash + canonical_hash
                     └──────────────────────┘
                                │
                                ▼
         ┌────────────────────────────────────────────┐
         │  pre_filter()  — deterministic, no LLM     │
         │  role classify → seniority → geo → remote  │
         │  comp anchor → skill overlap → score       │
         └────────────────────────────────────────────┘
                    │                      │
             pass_hard_filters       reasons_failed
                    │                      │
                    │                      └─→ dropped_by_reason counter (stats)
                    ▼
              ┌───────────────┐
              │  Postgres     │  jobs table + discovered_companies
              │  (in-memory   │
              │   fallback)   │
              └───────────────┘
                    │
                    ▼
         ┌────────────────────────────────────────────┐
         │  qualify_pending()  — LLM tier             │
         │  provider: OpenRouter → Kimi K2 Thinking   │
         │  mode: single-turn OR agent (tool loop)    │
         │  idempotent per resume_hash                │
         └────────────────────────────────────────────┘
                    │
                    ▼
       ┌──────────────────────────────┐
       │  QualificationResult (JSONB) │  fit_score, verdict, strengths,
       │                              │  gaps, positioning, evidence
       └──────────────────────────────┘
                    │
        ┌───────────┴────────────┐
        ▼                        ▼
  ┌──────────┐            ┌──────────────┐
  │  digest  │            │  draft <id>  │
  │  (html/  │            │  (cover      │
  │   md/    │            │   letter +   │
  │   text)  │            │   bullets +  │
  └──────────┘            │   Q&A)       │
        │                 └──────────────┘
        │                        │
        ▼                        ▼
     Kelyn                  drafts/*.md
        │                        │
        ▼                        ▼
   ┌──────────────────────────────────┐
   │  user_verdict / status / stage / │  ← state machine
   │  outcome                          │
   └──────────────────────────────────┘
        │
        ▼
   Feedback loop → past_verdicts → next qualify pass (few-shot)
```

## Module boundaries

The rule of thumb: **each package should be independently substitutable.**

### `ingestion/`

Everything before "does this row deserve LLM budget?" No LLM imports,
no OpenAI/Anthropic SDK references. If ingestion needs an LLM decision,
it doesn't — that's a signal the boundary is wrong.

Public surface:
- `FETCHERS` dict
- `run(storage)` orchestrator function
- `pre_filter(job, profile)` — cheap, deterministic
- `Storage` class
- `Job`, `PreFilterResult`, `RoleCategory`, `Seniority`, `RemoteStatus`

### `qualification/`

Everything "does this row match Kelyn?" The only place LLM calls happen
for filtering.

Public surface:
- `get_qualifier(provider, model)` → `QualifierClient`
- `qualify_pending(storage, client, resume)` — batch runner
- `qualify_one(client, ...)` — single-turn
- `qualify_one_agent(client, ...)` — tool loop
- `QualificationResult` pydantic model
- `Tool`, `ToolRegistry`, `default_registry()`

### `drafting/`

Everything "produce something Kelyn will send." Also LLM-backed.

Public surface:
- `draft_for_job(client, resume, job_row, qualification)` → `(DraftPackage, Path)`
- `DraftPackage` pydantic model

Additional surfaces in `drafting/`: `keywords.py` (deterministic ATS
keyword-gap scoring — resume gaps feed the draft prompt, final coverage
persists to `draft_keyword_coverage`), `prep.py` (interview prep pack:
gap-derived questions + dossier-grounded questions to ask), and
`followup.py` (dossier-hooked notes for silent applications).

### `intel/`

Cached company dossiers: public probes (GitHub org, Wikipedia, public
org members as warm-path candidates) → `company_intel` table, TTL 14
days. One fetch, many consumers (prep, follow-up, agent tools). Probes
are tolerant — a dead probe degrades the dossier, never raises.

Public surface:
- `get_company_intel(storage, company, force_refresh=False)` → dossier
- `find_warm_paths(storage, company)` → candidate list
- `dossier_text(intel)` → prompt-ready rendering

### `slackbridge/`

The two-way Slack surface (ADR 0010). Push half (`SlackClient`,
`blocks.py`) is httpx-only and works in cron; pull half (`listener.py`,
Socket Mode) needs `uv sync --extra slack`. `commands.py` maps inbound
verbs onto the same Storage/runner/memory calls as the CLI and MCP
server — third thin adapter, same sync rule.

Public surface:
- `SlackClient.post_message(channel, text, blocks)`
- `handle_command(text, storage=..., memory=...)` → mrkdwn reply
- `python -m slackbridge` — the listener daemon

### `autopilot/`

The continuous hunt (ADR 0012). One pass = `autopilot_candidates`
(qualified, fit >= floor, unreviewed, not in the state machine) →
`draft_for_job` per candidate → Slack review card (`pack_blocks`) with
Approve/Skip/Applied buttons → Notion push. Button clicks arrive as
`interactive` Socket Mode envelopes and route through
`slackbridge/interactions.py` onto the same Storage transitions as the
text verbs. Guardrails: fit floor, per-run cap (0 disables), drafted
jobs leave the pool (no double billing), and karani never submits.

Public surface:
- `run_autopilot(storage, slack=..., channel=..., make_qualifier=...,
  load_resume=..., min_fit=85, max_drafts=3)` → `AutopilotStats`

### `notionsync/`

The Notion job-hunt board (ADR 0011): one-way mirror, Postgres stays the
source of truth. `client.py` is Notion REST over httpx; `sync.py` holds
`init_database` (karani owns the schema), `sync_jobs` (full reconcile,
runs in `daily-full`), and `maybe_sync_job` (best-effort live push after
every state change — no-ops unconfigured, never raises). Page identity is
`notion_page_id` on the job row: create once, PATCH after, recreate on
404.

Public surface:
- `NotionClient`, `init_database(client, parent_page_id)`
- `sync_jobs(storage, client, database_id)` → counts
- `maybe_sync_job(storage, job_id)` → bool

### `memory/`

The memory layer (full doc: `docs/memory.md`, decision: ADR 0009).
`MemoryManager` is the single interface: `remember`/`recall` plus the
event composers `remember_verdict`/`remember_outcome` and the per-decision
`recall_for_job`. Modes `off | basic | mem0` via `KARANI_MEMORY`; the
`memories` ledger lives on `Storage` (system of record), mem0+pgvector is
a derived semantic index that degrades to deterministic recall on any
failure. Qualification consumes recall via the `<memories>` prompt block
(`qual-v2`).

Public surface:
- `MemoryManager(storage, mode=None)`
- `remember(content, kind, ...)`, `recall(query, ...)`,
  `remember_verdict(row, v)`, `remember_outcome(row, o)`,
  `recall_for_job(row)`

### `mcp_server/`

The interactive interface layer — an MCP server (official `mcp` SDK 2.x,
stdio) exposing 25 tools that map 1:1 onto the CLI verbs. Strictly a thin
adapter: tools call the same runners and `Storage` methods the CLI calls,
and hold no business logic of their own. Storage is a lazily-connected
process-wide singleton so the in-memory fallback keeps state across tool
calls; `use_storage()` injects one for tests/embedding, and
`_make_qualifier` / `_load_resume` are the seams for faking the LLM and
resume. Expected user-input failures raise `ToolError` so the message
survives the SDK's exception masking. See ADR 0008.

Public surface:
- `app` — the `MCPServer` instance (`python -m mcp_server` serves stdio)
- `use_storage(storage)` — inject/reset the storage singleton

### Cross-package contract

The `Job` and `QualificationResult` pydantic models are the *only*
data structures shared across packages. If you need a new field on
either, it goes through both packages plus a storage migration.

## Storage schema at a glance

Single table: `jobs`. See `ingestion/storage.py` for the full DDL. Columns
grouped by concern:

- **Provenance**: `source`, `source_id`, `content_hash`, `canonical_hash`
- **Job**: `company`, `company_display`, `title`, `department`, `team`,
  `location_raw`, `remote_status`, `description_text`, `apply_url`,
  `posted_at`, `tags`, `raw` JSONB
- **Comp**: `comp_min_usd`, `comp_max_usd`, `comp_disclosed`,
  `comp_currency_original`
- **Pre-filter**: `prefilter` JSONB, `prefilter_passed`, `prefilter_score`,
  `role_category`, `seniority`
- **Qualification**: `qualification` JSONB, `verdict`, `fit_score`,
  `qualified_at`, `qualification_resume_hash`
- **User feedback**: `user_verdict`, `user_verdict_at`
- **State machine**: `application_status`, `applied_at`, `stages` JSONB,
  `outcome`, `outcome_at`, `draft_path`
- **Lifecycle**: `active`, `closed_at`, `first_seen_at`, `last_seen_at`,
  `updated_at`

Auxiliary table: `discovered_companies` for the reverse-ATS probe.

## Extension points

### Adding an LLM provider

Two provider files exist: `qualification/openrouter.py` and
`qualification/anthropic.py`. They implement the informal `QualifierClient`
protocol:

```python
class QualifierClient(Protocol):
    model_name: str
    async def complete(self, system: str, user: str) -> str: ...
    # optional, for agent mode:
    async def chat_turn(self, messages, tools=None) -> dict: ...
```

To add a new provider:
1. `qualification/<provider>.py` with a class implementing `complete()`
   (and `chat_turn()` if you want agent mode).
2. Register it in `qualification/factory.py:get_qualifier`.
3. Guard the SDK import at construction time (see `anthropic.py`).
4. Add a smoke test in `tests/test_qualification.py`.

### Adding an ingestion source

`ingestion/base.py:Fetcher` is the ABC. Every concrete source implements:

```python
class Fetcher(ABC):
    source: Source  # enum value
    async def fetch(self, client: httpx.AsyncClient, slug: str | None = None) -> list[Job]: ...
```

- Use `get_with_retry` from `base.py` — never call `client.get` directly.
- Every `Job` must go through `.finalize()`.
- Register in `ingestion/__init__.py:FETCHERS`.
- Add to `FEED_SOURCES` or `TARGETS` in `targets.py`.
- Write a smoke test with a canned JSON payload.

### Adding an agent tool

`qualification/tools.py:Tool` is a dataclass:

```python
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema
    fn: Callable[..., Awaitable[str]]
```

Rules:
- Return text, not exceptions. Wrap errors as `"ERROR: ..."` strings.
- Enforce a byte cap via `_truncate`.
- Enforce a timeout via the caller (`ToolRegistry.execute` wraps in
  `asyncio.wait_for`).
- Append to `DEFAULT_TOOLS`.
- Update `AGENT_SYSTEM_PROMPT` in `qualification/prompts.py` and bump
  `AGENT_PROMPT_VERSION`.

### Adding a state-machine transition

`ingestion/storage.py:Storage.APPLICATION_STATUSES` is the source of
truth. To add a new status:
1. Add to the frozenset.
2. Add a CLI subcommand in `ingestion/cli.py` if it needs a dedicated
   command (usually not — `status` covers it).
3. If the transition needs side effects (e.g. `applied` bumps
   `applied_at`), extend `set_application_status`.
4. Test in `tests/test_storage.py`.

## Runtime characteristics

- **Ingestion cost:** free (public APIs, some rate-limited). Wall time
  ≈ 30–60s for a full 9-source pass.
- **Qualification cost:** ~$0.01 per row single-turn, ~$0.05–$0.15 per
  row agent-mode (Kimi K2 Thinking). Concurrency capped at 3 for agent,
  5 for single-turn.
- **Drafting cost:** ~$0.03 per draft. Not batched — done on demand.
- **Storage:** Neon Postgres (or in-memory for local runs). Ballpark
  ~10k rows at steady state; keeps well under free tier.

## Failure modes we've hardened against

- **404 on a slug that changed ATS** → per-source outcome logged, run
  continues.
- **Feed schema drift** → `raw` column preserves original; write a
  reparser script when a source evolves.
- **LLM returns malformed JSON** → `_extract_json` strips think-tags,
  fences, prose prefixes; malformed → `verdict="maybe"` with the raw as
  evidence.
- **DB down** → in-memory fallback lets tests and demos run.
- **Concurrent qualification runs** → idempotent per `resume_hash` +
  `content_hash`; safe to run overlapping.
- **Agent loop runaway** → `max_iterations` + `max_tool_calls` caps;
  loop nudges to final JSON when budget hits.

## Known failure modes we haven't fixed

See `docs/roadmap.md`. Highlights:
- No persistent per-source health metrics.
- No per-run cost tracking beyond OpenRouter's own dashboard.
- No retry of failed qualifications on a subsequent run (they get
  re-selected but if the model is having a bad day, we'll re-fail).
