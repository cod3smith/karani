# CLAUDE.md — karani

This file is the entry point for any Claude Code (or coding-agent) session on this
repo. Read it first, then follow the pointers into `docs/` for depth.

If you change something material — architecture, positioning, a public interface,
or a rule below — update this file in the same commit. Docs drift is worse than
no docs.

---

## 1. What this is

**karani** is a semi-autonomous, personal job-application pipeline for **Kelyn
Njeri** (Nairobi-based senior/staff engineer with a data-platform + causal-ML
background). It:

1. **Ingests** postings from 9 sources (Greenhouse, Lever, Ashby, Workable,
   RemoteOK, Himalayas, We Work Remotely, Remotive, aijobs.net).
2. **Pre-filters** deterministically for role fit + seniority + geo + comp +
   skill overlap. Drops ~95% before any LLM call.
3. **Qualifies** survivors against Kelyn's resume via LLM (OpenRouter →
   `moonshotai/kimi-k2-thinking` by default; single-turn or tool-using agent
   loop). Emits `fit_score`, `verdict`, evidence-backed strengths, gaps,
   recommended positioning.
4. **Digests** the shortlist as HTML / markdown / text.
5. **Drafts** cover letters + tailored bullets + application-question answers
   in Kelyn's voice.
6. **Tracks** the application through a state machine (drafting → applied →
   interviewing → offer/rejection).
7. **Learns** from Kelyn's reactions via a `user_verdict` feedback loop that
   feeds past pairs back as few-shot examples on the next qualify run.
8. **Serves** the whole pipeline as an MCP server (`mcp_server/`, stdio) so
   MCP clients can drive ingest/qualify/shortlist/draft/track
   conversationally. See `docs/adrs/0008-mcp-server-interface.md`.
9. **Remembers** distilled facts (preferences, company intel, outcomes)
   in a ledger-first memory layer (`memory/`) and injects them into
   qualification. mem0 + pgvector optional upgrade. See `docs/memory.md`.
10. **Converses** over Slack (`slackbridge/`, Socket Mode): pushes the
    digest/worklist and takes the same verbs back (`verdict 123 apply`,
    `prep 45`). See `docs/adrs/0010-slack-two-way-surface.md`.
11. **Converts**: fast-lane flags on fresh high-fit roles, ATS keyword
    coverage per draft, cached company dossiers (`intel/`), warm-path
    candidates, interview prep packs, and dossier-hooked follow-ups —
    all measured through `funnel_stats` (roadmap Tier 1.5).

**Positioning:** target *companies that hire globally at SF pay bands
regardless of candidate location*. Everything downstream assumes that thesis.
See `docs/vision.md`.

---

## 2. Repo layout

```
karani/
├── CLAUDE.md                        ← you are here
├── README.md                        ← human-facing quickstart
├── Makefile                         ← daily / ingest / qualify / digest chains
├── pyproject.toml
├── .env.example
│
├── ingestion/                       ← fetch → pre-filter → store → sweep
│   ├── config.py                    ← signals, thresholds, env
│   ├── profile.py                   ← UserProfile: seniority bands, must-have skills
│   ├── models.py                    ← Job, PreFilterResult, RoleCategory, Seniority
│   ├── roles.py                     ← deterministic role + seniority classifier
│   ├── filters.py                   ← pre_filter(): the hard gate
│   ├── base.py                      ← HTTP retry, per-host semaphores, HTML strip
│   ├── {greenhouse,lever,ashby,workable}.py   ← per-company ATS fetchers
│   ├── {remoteok,himalayas,weworkremotely,remotive,aijobs}.py   ← feed fetchers
│   ├── targets.py                   ← curated company list + FEED_SOURCES
│   ├── orchestrator.py              ← run() = fetch → filter → upsert → sweep
│   ├── storage.py                   ← Postgres + in-memory fallback
│   ├── digest.py                    ← text / md / HTML renderers
│   ├── discovery.py                 ← reverse-ATS probe for auto-promotion
│   ├── resume.py                    ← ResumeProfile from data/resume.md
│   └── cli.py                       ← argparse entry
│
├── qualification/                   ← LLM fit analysis
│   ├── models.py                    ← QualificationResult (pydantic)
│   ├── prompts.py                   ← versioned prompts + few-shot renderer
│   ├── client.py                    ← QualifierClient protocol + JSON extraction
│   ├── openrouter.py                ← default provider (Kimi K2 Thinking)
│   ├── anthropic.py                 ← optional direct-Anthropic provider
│   ├── local.py                     ← local OpenAI-compatible provider (Ollama etc.)
│   ├── factory.py                   ← get_qualifier() dispatches by env
│   ├── tools.py                     ← web_search / fetch_url / github_org / wikipedia
│   ├── agent.py                     ← tool-using multi-turn loop
│   └── runner.py                    ← qualify_pending() orchestration
│
├── drafting/                        ← cover letter + bullets + Q&A + prep + follow-up
│   ├── models.py                    ← DraftPackage
│   ├── prompts.py                   ← versioned drafting prompt
│   ├── keywords.py                  ← deterministic ATS keyword-gap scoring
│   ├── prep.py                      ← interview prep pack (prep-v*)
│   ├── followup.py                  ← dossier-hooked follow-up notes (followup-v*)
│   ├── writers.py                   ← markdown emitter
│   └── runner.py                    ← draft_for_job()
│
├── intel/                           ← cached company dossiers + warm paths
│   └── service.py                   ← probes → company_intel table (TTL 14d)
│
├── slackbridge/                     ← two-way Slack surface (ADR 0010)
│   ├── client.py                    ← Web API via httpx (push; no SDK)
│   ├── blocks.py                    ← Block Kit for digest/actions pushes
│   ├── commands.py                  ← inbound verb dispatcher (thin adapter)
│   └── listener.py                  ← Socket Mode daemon (`--extra slack`)
│
├── memory/                          ← memory layer (docs/memory.md)
│   └── manager.py                   ← MemoryManager: off | basic | mem0 modes
│
├── mcp_server/                      ← MCP interface (stdio) over the pipeline
│   ├── server.py                    ← MCPServer `app` + 22 tools; thin adapter
│   └── __main__.py                  ← `python -m mcp_server` / `make mcp`
│
├── docker-compose.yml               ← dedicated infra: pgvector Postgres (+ Ollama profile)
│
├── data/
│   ├── resume.md                    ← Kelyn's resume (source of truth for LLM)
│   ├── resume.md.example
│   └── digest.html                  ← generated by `make digest`
│
├── drafts/                          ← generated cover letters + tailored bullets
│
├── tests/                           ← pytest suite (121+ tests, all deterministic)
│   ├── conftest.py
│   ├── test_{filters,roles,storage,qualification,agent,drafting,digest,discovery}.py
│   ├── test_e2e_pipeline.py         ← mocked-HTTP end-to-end integration test
│   └── test_mcp_server.py           ← every MCP tool via server.call_tool
│
└── docs/
    ├── vision.md                    ← the "why" — positioning, personas, non-goals
    ├── architecture.md              ← data flow + module boundaries + extension points
    ├── roadmap.md                   ← what's next, prioritized, with acceptance criteria
    ├── memory.md                    ← memory architecture: ledger, mem0, recall rules
    ├── conventions.md               ← code style, testing patterns, PR checklist
    ├── operations.md                ← env, cron, secrets rotation, monitoring
    └── adrs/                        ← architecture decision records
```

---

## 3. Getting oriented in 60 seconds

```bash
# One-time setup
uv sync                                        # or pip install -e .
uv sync --extra dev                            # + pytest
cp .env.example .env                           # fill DATABASE_URL, OPENROUTER_API_KEY
cp data/resume.md.example data/resume.md       # edit it — this is YOU

# Daily loop
make daily                                     # ingest + qualify + digest
open data/digest.html                          # or send it to yourself

# Act on a suggestion
python -m ingestion.cli draft 12345            # writes drafts/*.md
python -m ingestion.cli status 12345 applied
python -m ingestion.cli verdict 12345 apply    # feeds the taste-calibration loop

# Or drive it all over MCP (Claude Code picks up .mcp.json automatically)
make mcp                                       # python -m mcp_server (stdio)
```

Full CLI reference is in `README.md`. Full command surface is in `Makefile`.

---

## 4. Non-negotiable rules

These aren't preferences — they're guardrails you must respect. Break one, and
you'll silently degrade the pipeline in a way that's hard to spot.

### 4.1 Never regress the SF-band, global-remote thesis
- `ingestion/config.py` signals are named `global_hire_*`, `regional_restriction_*`,
  `pay_parity_*`. **Do not** reintroduce Kenya-specific names — they're the whole
  reason the pipeline exists, but the *filter* is location-agnostic (a US-only
  role gets vetoed whether Kelyn's in Nairobi, Berlin, or Buenos Aires).
- `min_comp_usd` default is $160k. Below that = not SF-band = drop.

### 4.2 Signal matching is word-boundary anchored
- Use `_find_signal` in `filters.py` (or the `_wb()` helper in `roles.py`).
- **Never** substring-match location or role phrases. "us only" matches "campus
  only" as a substring — that's the exact bug that made the pipeline useless
  before the refactor. There's a regression test in `tests/test_filters.py`.

### 4.3 Comp parsing requires a currency anchor
- `_parse_comp` in `filters.py` only accepts a number band when a currency /
  comp keyword is within 40 chars. Do not weaken this — you'll parse "5-10
  years experience" as $5k-$10k and hard-fail every role.

### 4.4 Content hash is normalized
- `Job.compute_hash` lowercases + strips punctuation + collapses whitespace
  before hashing. If you change the hash inputs, previously-qualified jobs
  will *all* re-qualify (billed). If that's intentional, say so in the commit.

### 4.5 Every fetcher must go through `get_with_retry`
- `ingestion/base.py`. Enforces per-host semaphore + tenacity backoff + 404
  fast-fail. Do not write a fetcher that calls `client.get` directly.

### 4.6 Every fetched `Job` must call `.finalize()`
- Populates `content_hash` and `canonical_hash`. Without these, upsert dedup
  and cross-source dedup silently break.

### 4.7 Storage schema changes go through `ALTER TABLE ... IF NOT EXISTS`
- `ingestion/storage.py` runs the `SCHEMA` block on every connect. It's
  idempotent. Never write a destructive migration — Kelyn's Neon DB has
  history in it now.

### 4.8 Credentials come from env vars only
- `config.py` reads `os.getenv(...)`. Never hardcode a DSN, API key, or token
  in code. The `.env.example` shows the shape.

### 4.9 The pre-filter is deterministic
- No LLM calls in `filters.py` or `roles.py`. That's the whole point of the
  tier split — pre-filter is free and fast, qualification is billed. Don't
  cross the streams.

### 4.10 Prompts are versioned
- `qualification/prompts.py` has `PROMPT_VERSION`, `AGENT_PROMPT_VERSION`.
  `drafting/prompts.py` has `DRAFT_PROMPT_VERSION`. Bump them when you
  materially change a prompt — persisted `QualificationResult` rows carry
  the version, and that's how we distinguish "old data" from "new data" for
  A/B and rollback.

### 4.11 The memory ledger is the system of record
- The `memories` table in Postgres is ground truth; the mem0/pgvector
  index is derived and disposable. Never write memory only to mem0, and
  never let a mem0 failure abort a batch — degrade to `basic` recall.
  See `docs/memory.md` and ADR 0009.
- Memory writes happen on explicit events (verdict, outcome, deliberate
  `remember`) — never auto-extracted from arbitrary LLM output.

---

## 5. Coding conventions

Full details in `docs/conventions.md`. The one-liners:

- **Python 3.11+**, no exceptions.
- **Async everywhere** in ingestion + qualification. Blocking `time.sleep` or
  sync `requests` calls will break the concurrency model.
- **Pydantic v2** for every structured payload that crosses a boundary.
- **`from __future__ import annotations`** at the top of every module.
- **Type hints everywhere.** Use `dict`, `list`, `str | None` — not
  `Dict`, `List`, `Optional`.
- **Guard optional imports.** `qualification/anthropic.py` imports the
  Anthropic SDK inside `__init__` so the module loads even when the SDK
  isn't installed. Follow the same pattern for any future optional dep.
- **Errors from LLMs are recoverable.** `qualify_one` on malformed JSON
  downgrades to `verdict="maybe"` with a `why_skip` explanation. Never let
  a bad LLM response throw and kill a batch.
- **No emojis in code or docs** unless explicitly requested.

---

## 6. Testing conventions

- Run: `make test` or `pytest tests -q`.
- Every new module needs a smoke test at minimum.
- All 121 existing tests are deterministic (no network, no clock). Keep it
  that way — use fake clients for LLM calls (see `tests/test_qualification.py`
  and `tests/test_agent.py`), and `httpx.MockTransport` for HTTP (see
  `tests/test_e2e_pipeline.py`).
- MCP tools are tested through `app.call_tool` — full validation +
  execution + serialization, no transport (see `tests/test_mcp_server.py`).
- `pytest-asyncio` is auto-mode (see `pyproject.toml`). Just decorate coroutines
  with `@pytest.mark.asyncio`.
- The in-memory `Storage` fallback (`Storage("")`) is the standard test
  substrate — no Postgres required.

---

## 7. Where to work next

Prioritized list is in **`docs/roadmap.md`** with acceptance criteria per item.
Summary:

1. **Cron / scheduled runs** — `Makefile` is ready; wire it to `launchd`
   (mac) or `systemd`/`cron` (linux). Half-day.
2. **Neon credential rotation** — the DSN was in `config.py` at one point
   before the refactor. Rotate it. 10 minutes on Kelyn's side, but has to
   happen before real ops.
3. **Persistent per-source health metrics** — right now the run stats are
   ephemeral. Add a `source_runs` table + a `stats history` CLI. Half-day.
4. **Digest as email / Slack / Cowork artifact** — HTML file exists. Wire
   one delivery channel (SES, Slack webhook, or a Cowork artifact). Day.
5. **Levels.fyi comp overlay** — where comp isn't disclosed, backfill via
   a scrape/API. Feeds `fit_score`. Day.
6. **Agent tool-use expansion** — add `check_glassdoor_reputation`,
   `fetch_recent_news`, `check_engineering_blog_recency`. Half-day per tool.

Bigger unlocks in `docs/roadmap.md`.

---

## 8. Where to find the "why" for decisions

`docs/adrs/` — one file per key decision, why-based (context + decision +
consequences). Don't argue architecture with the code; argue it with an ADR.
If you're about to reverse a decision documented in an ADR, add a new ADR
that supersedes it. Never delete.

---

## 9. Working with Kelyn

- Kelyn is a **staff/principal-band engineer** (see `data/resume.md`). Talk
  to him accordingly — skip explaining Python basics, but do explain
  domain-specific tradeoffs (ATS quirks, prompt-engineering choices, DB
  schema evolution).
- Timezone: **EAT (UTC+3)**. Async-friendly.
- Preferences: **concise, direct, no fluff, no emojis unless he asks.**
  Prose over bullet lists in normal conversation; bullet lists for real
  structured deliverables (this doc, roadmap, ADRs).
- He'll push back if you sugar-coat. Be blunt about tradeoffs.

---

## 10. Fast paths for common tasks

### Add a new ingestion source
1. Add enum to `Source` in `ingestion/models.py`.
2. Write `ingestion/<name>.py` — subclass `Fetcher`, use `get_with_retry`,
   call `.finalize()` on every `Job`.
3. Register in `ingestion/__init__.py:FETCHERS`.
4. If per-company: add slugs to `TARGETS` in `targets.py`. If feed: add to
   `FEED_SOURCES`.
5. Write a test with a fake JSON payload.

### Change what the pre-filter drops
1. Edit `ingestion/filters.py` `pre_filter()`.
2. Add / edit signals in `ingestion/config.py`.
3. Add a regression test in `tests/test_filters.py`.
4. If the change affects role classification: also touch `ingestion/roles.py`
   and `tests/test_roles.py`.

### Add a new LLM tool for the agent
1. Add a function to `qualification/tools.py` — return text, wrap errors,
   respect the byte cap.
2. Append a `Tool(...)` entry to `DEFAULT_TOOLS`.
3. Update the agent system prompt in `qualification/prompts.py`
   (`AGENT_SYSTEM_PROMPT`) and bump `AGENT_PROMPT_VERSION`.
4. Write a smoke test that scripts a `chat_turn` returning a `tool_calls`
   block invoking your tool.

### Add a new MCP tool
1. Put any new query/mutation on `Storage` (or a runner) first — the MCP
   layer stays a thin adapter, same as the CLI.
2. Add an `@app.tool()` function in `mcp_server/server.py`. Raise `ToolError`
   for expected user-input failures (plain exceptions get masked by the SDK).
3. Add a test in `tests/test_mcp_server.py` via `app.call_tool`, and update
   the tool-set assertion in `test_tool_listing`.
4. If the tool mirrors a CLI verb, keep the two surfaces in sync.

### Swap LLM providers
1. Either set `QUAL_PROVIDER=anthropic|local` / `QUAL_MODEL=...` in env, or
   pass `--provider` / `--model` to `qualify` and `draft`. `local` speaks to
   any OpenAI-compatible server (Ollama/LM Studio/vLLM) — zero token cost;
   recommended for bulk qualify, with a hosted model kept for drafting.
2. To add a *new* provider: subclass into `qualification/<provider>.py`,
   implement `complete()` (and `chat_turn()` if you want agent-mode
   support), register in `qualification/factory.py`.

---

## 11. Things that look wrong but aren't

- **`ingestion/sources.py` re-exports `FETCHERS` from `__init__.py`.** Legacy
  compat shim. Don't delete without grep — external scripts might import
  from there.
- **`main.py` at root delegates to `ingestion.cli.main`.** Same reason.
- **`comp_currency_original="USD"` when comp is undisclosed and unknown.**
  Deliberate — the RemoteOK feed omits currency, so we accept as USD only
  when the number falls in a plausible US-band range. See the docstring in
  `remoteok.py`.
- **`stages` is a JSONB append log, not a normalized `interview_stages`
  table.** Deliberate simplicity — see `docs/adrs/`.

---

Last audit: this file must be re-read whenever any of the following change:
`pyproject.toml`, `ingestion/config.py`, `ingestion/models.py`,
`qualification/prompts.py`, or `docs/roadmap.md`.
