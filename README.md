# karani — ingestion

Fetches job postings from nine sources, classifies the role, extracts geo/comp/culture signals, and stores rows for downstream LLM qualification.

## Positioning

Target: **companies that hire globally at SF pay bands, regardless of candidate location.** Everything downstream assumes that thesis.

- Hard gates: senior/staff engineering role, remote (not hybrid), region-locked jobs vetoed, comp ≥ $160k where disclosed.
- Nice-to-have: explicit pay parity language, retreat/travel budget, must-have skill overlap.
- Postings are scored 0–100 for ranking after they pass the hard gates.

## Sources

**ATS (per-company slug):** Greenhouse, Lever, Ashby, Workable
**Global feeds:** RemoteOK (tag-scoped), Himalayas (category-scoped), Remotive (category-scoped), We Work Remotely (already programming-only)
**Domain-specific:** aijobs.net (AI/ML board)

## Layout

```
ingestion/
├── models.py           # Job, PreFilterResult, RoleCategory, Seniority
├── config.py           # thresholds, signals, env-driven DSN
├── profile.py          # user profile (Kelyn: senior+, must-have skills)
├── roles.py            # deterministic role + seniority classifier
├── targets.py          # curated company list (pay parity / global hire tags)
├── filters.py          # pre-filter (role → seniority → geo → remote → comp)
├── base.py             # HTTP retry, per-host semaphores, shared helpers
├── storage.py          # Postgres upsert + stale sweep
├── orchestrator.py     # fetch → pre-filter → upsert → sweep, w/ observability
├── cli.py              # run | qualify | digest | verdict | stats | sweep
├── resume.py           # ResumeProfile loader (data/resume.md)
├── greenhouse.py       # ATS fetchers
├── lever.py
├── ashby.py
├── workable.py
├── remoteok.py         # feed fetchers
├── himalayas.py
├── weworkremotely.py
├── remotive.py
└── aijobs.py

qualification/          # LLM-tailored fit analysis against your resume
├── models.py           # QualificationResult (pydantic)
├── prompts.py          # versioned system+user prompt
├── client.py           # Anthropic-backed qualifier + JSON extraction
└── runner.py           # DB pending → LLM → DB write, concurrency-bounded

data/
└── resume.md           # your resume in markdown (see resume.md.example)
```

## Pipeline stages

1. **Fetch** — per-host semaphores (default 3), global concurrency 6, tenacity-backed retries on 5xx/429. Fetch errors surface per source.
2. **Classify** — deterministic `RoleCategory` (SWE, ML_AI, DATA, DEVOPS_SRE, SECURITY, RESEARCH, ...) + `Seniority`. Runs on title, then tags, then a description sample.
3. **Pre-filter** — word-boundary signal match on geo, remote, pay parity, comp anchored to currency keywords, skill overlap against the user profile. Hard-fails collected as `reasons_failed`.
4. **Cross-source dedup** — same `canonical_hash` (company + normalized title + posted-week) is suppressed within a run.
5. **Upsert** — Postgres, batched via `asyncio.gather` on a bounded semaphore. `active=TRUE`, `closed_at=NULL` on every touch.
6. **Sweep** — jobs not seen for `stale_job_days` (default 10) get `active=FALSE, closed_at=NOW()`. Prevents applying to dead reqs.
7. **Qualify** (separate command) — top-scored pending rows go to an LLM with your full resume + hints. Default provider is **OpenRouter** with `moonshotai/kimi-k2-thinking` at `reasoning_effort=high`. Returns `fit_score` (0–100), `verdict` (qualified|maybe|skip), evidence-backed strengths, gaps with mitigations, red flags, why-apply, and recommended positioning. Idempotent per resume hash — change your resume and re-qualify.
8. **Digest** — top qualified/maybe rows for the day, sorted by `fit_score`. Wire this to email/Slack/artifact later.
9. **Feedback** — `verdict` command records your reaction (`apply|shortlist|later|skip|applied`) so downstream tuning has ground truth.

## Configure

```bash
cp .env.example .env                          # fill DATABASE_URL + OPENROUTER_API_KEY
cp data/resume.md.example data/resume.md      # then edit it — this is YOU
uv sync                                        # or pip install -e .

# --- ingest + rank ---
python -m ingestion.cli run                    # fetch + pre-filter + sweep + discover
python -m ingestion.cli qualify --limit 50     # single-turn qualify
python -m ingestion.cli qualify --agent --limit 5   # tool-using agent (top-tier only)

# --- act on the shortlist ---
python -m ingestion.cli digest --format html --output data/digest.html
python -m ingestion.cli draft 12345            # cover letter + bullets + Q&A → drafts/*.md
python -m ingestion.cli verdict 12345 apply    # taste signal for future qualify runs

# --- application state machine ---
python -m ingestion.cli status 12345 applied
python -m ingestion.cli stage 12345 recruiter_screen --notes "30-min chat"
python -m ingestion.cli outcome 12345 offer

# --- housekeeping ---
python -m ingestion.cli discover               # probe unpromoted companies for ATS presence
python -m ingestion.cli sweep --days 14
python -m ingestion.cli stats
python -m ingestion.cli actions                # what to do next: review/draft/submit/follow up
python -m ingestion.cli funnel                 # response/interview/offer conversion rates
```

Or use the Makefile — `make daily` chains `ingest + qualify + digest`.

Typical cron: `run` every 2–4h, `qualify` every 4–8h, `digest` in your morning brief.

All tuning knobs are env-driven (see `.env.example` and `config.py`).

## MCP server

The whole pipeline is exposed as an MCP server (stdio), so any MCP client —
Claude Code, Claude Desktop, Cowork — can drive the daily loop
conversationally:

```bash
make mcp            # or: python -m mcp_server
```

The repo ships a project-scoped `.mcp.json`, so Claude Code sessions opened
in this directory pick the server up automatically.

Tools map 1:1 onto the CLI verbs:

| Tool | Does |
| --- | --- |
| `ingest`, `sweep` | fetch + pre-filter + store; close stale jobs |
| `discover` | probe feed-discovered companies for ATS boards, promote hits |
| `qualify` | LLM-qualify pending rows (billed; `agent_mode` opt-in) |
| `digest`, `shortlist`, `get_job` | review surface — rendered or structured |
| `draft` | cover letter + bullets + Q&A to `drafts/*.md` (billed) |
| `record_verdict` | taste signal for the few-shot feedback loop |
| `set_status`, `add_stage`, `record_outcome` | application state machine |
| `pipeline_stats` | DB counts + funnel |
| `next_actions` | prioritized worklist: review, draft, submit, follow up |
| `funnel_stats` | response/interview/offer rates by fit band, source, prompt version |
| `remember`, `recall` | teach/query the memory layer (see below) |

Storage is shared across tool calls (Postgres via `DATABASE_URL`, or the
in-memory fallback for a scratch session). See
`docs/adrs/0008-mcp-server-interface.md` for the design.

## Memory

Karani retains context and uses it at decision time — full architecture in
`docs/memory.md`. Short version: a deterministic `memories` ledger in
Postgres is the system of record; verdicts and outcomes write distilled
facts automatically; qualification recalls the relevant ones per job and
injects them as a `<memories>` prompt block. `KARANI_MEMORY=mem0` (with
`uv sync --extra memory` and the compose stack) upgrades recall to
semantic search via mem0 + pgvector, with extraction/embeddings on local
Ollama — zero token cost. Any mem0 failure degrades to the deterministic
path; nothing is ever lost.

```bash
python -m ingestion.cli remember "PostHog's screen asked about incident ownership" --kind question --company PostHog
python -m ingestion.cli recall "PostHog interview" --limit 5
```

## Infrastructure

Dedicated, disposable, local:

```bash
make infra-up        # Postgres + pgvector on localhost:5433
make infra-up-llm    # + Ollama on localhost:11434 (local LLM + memory extraction)
make infra-psql      # shell into the DB
make infra-down      # stop (volumes persist)
```

Point `DATABASE_URL` at `postgresql://karani:karani@localhost:5433/karani`
or keep Neon — the DSN is the only switch.

## LLM providers

The qualifier is provider-pluggable. Set once via env; override per-invocation with `--provider` / `--model`.

**OpenRouter (default).** Any OpenRouter model slug works — the current default is `moonshotai/kimi-k2-thinking` because Kimi K2 has strong long-context reasoning and OpenRouter exposes extended thinking via the standard `reasoning.effort` param. To swap models:

```bash
QUAL_PROVIDER=openrouter QUAL_MODEL=moonshotai/kimi-k2-thinking \
  python -m ingestion.cli qualify --limit 20
# or one-shot:
python -m ingestion.cli qualify --provider openrouter --model anthropic/claude-sonnet-4.5
```

Uses only `httpx` — no extra SDK needed. Reasoning tokens count toward completion; the default `QUAL_MAX_TOKENS=8000` allows for it.

**Anthropic direct.** Install with `uv sync --extra anthropic`, then:

```bash
QUAL_PROVIDER=anthropic QUAL_MODEL=claude-haiku-4-5-20251001 \
  python -m ingestion.cli qualify --limit 50
```

**Local (zero token cost).** Any OpenAI-compatible server — Ollama,
LM Studio, vLLM, llama.cpp. No API key. Agent mode works with local models
that support tool calling (qwen3, llama3.3):

```bash
QUAL_PROVIDER=local LOCAL_LLM_MODEL=qwen3:32b \
  python -m ingestion.cli qualify --limit 50
# mix and match: cheap local bulk qualification, strong hosted drafting
python -m ingestion.cli draft 12345 --provider openrouter
```

Recommended split: local for bulk qualification (high volume, forgiving),
hosted for drafting (low volume, and draft quality is what gets the
interview).

**Agentic follow-up.** The current qualifier is single-turn — one LLM call per job, structured JSON out. Kimi K2 supports tool-use, so a natural next step is to hand it a set of tools (`fetch_levels_fyi_comp(company)`, `fetch_company_blog(company)`, `check_engineering_hiring_signals(company)`) and let it decide whether to gather more evidence before ruling. The `OpenRouterQualifier.complete` payload builder is where you'd add `tools=[...]` and loop on `finish_reason=="tool_calls"`. Kept out of scope for the first pass — deterministic single-turn is enough to unblock the daily digest.

## What's downstream

```sql
SELECT id, title, company_display, description_text, apply_url,
       prefilter_score, role_category, seniority
  FROM jobs
 WHERE prefilter_passed = TRUE
   AND active = TRUE
   AND qualification IS NULL
 ORDER BY prefilter_score DESC, posted_at DESC;
```

Only rows where `prefilter_passed = TRUE`, `active = TRUE`, and (`qualification IS NULL` OR `qualification_resume_hash` != current) go to the LLM. Cost bound: Haiku ≈ $0.01/row, so a full pass on ~500 pre-filtered rows costs < $5.

## Feedback loop

Every reaction you record with `verdict` writes to `user_verdict` + `user_verdict_at`. Next iteration: feed the last 50 verdicts (as `[job → reaction]` pairs) back into the qualification prompt as few-shot examples so the model learns your taste over time without retraining. Table already carries the columns — the wire-up is one prompt change.

## Adding a source

1. Subclass `Fetcher` in `ingestion/<source>.py`.
2. Register in `ingestion/__init__.py` `FETCHERS` and add the enum to `Source`.
3. If it's a per-company ATS, add company slugs to `TARGETS`. If it's a feed, add the enum to `FEED_SOURCES`.
4. Every fetcher must use `get_with_retry` (per-host semaphore + backoff).
5. Every fetched job must call `.finalize()` so `content_hash` and `canonical_hash` are populated.

## Gotchas

- **RemoteOK / Himalayas / Remotive schemas drift.** `raw` is stored on every row — write a reparser when the parser evolves.
- **Ashby comp** comes in two shapes. Both handled; adds via `to_usd` for non-USD currencies.
- **Workable** requires a per-posting detail fetch; we only detail-fetch titles that pass a title regex to keep the request count sane.
- **Slugs go stale.** Per-source outcomes print in the CLI so 404s surface immediately.
- **RemoteOK salary currency** is often missing. We only accept undeclared salaries when they fall in a plausible USD range; anything outside that is treated as undisclosed.
- **Pay parity** is a positive signal, not a hard gate. Companies rarely state it in the job post itself; look at the score column, not `pay_parity`, for ranking.
