# karani

<!-- re-add once GitHub Actions billing is unlocked:
[![ci](https://github.com/cod3smith/karani/actions/workflows/ci.yml/badge.svg)](https://github.com/cod3smith/karani/actions/workflows/ci.yml) -->
[![CircleCI](https://dl.circleci.com/status-badge/img/gh/cod3smith/karani/tree/main.svg?style=shield)](https://dl.circleci.com/status-badge/redirect/gh/cod3smith/karani/tree/main)
[![Coverage Status](https://coveralls.io/repos/github/cod3smith/karani/badge.svg?branch=main)](https://coveralls.io/github/cod3smith/karani?branch=main)
[![PyPI](https://img.shields.io/pypi/v/karani)](https://pypi.org/project/karani/)
[![Python](https://img.shields.io/pypi/pyversions/karani)](https://pypi.org/project/karani/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A semi-autonomous, self-hosted job-hunt pipeline. It ingests postings
from nine sources every hour, qualifies them against *your* resume with
an LLM, drafts a complete application pack (tailored resume + cover
letter, de-AI'd by a measured humanizer), and delivers it to Slack as a
review card with Approve / Skip / Applied buttons. You press submit —
karani never does (see `docs/vision.md` non-goals).

Everything is optional and degrades gracefully: it runs end-to-end with
zero external services, and upgrades piecewise with Postgres, Slack,
Notion, MinIO, mem0 + pgvector semantic memory, local Ollama models, and
LangGraph orchestration. Every intelligence feature reports to a
conversion-funnel metric so improvements are measured, not vibed.

> **Why "karani"?** *Karani* (kah-RAH-nee) is Swahili for **clerk** —
> the diligent office worker who files the paperwork, keeps the
> records, and never misses a deadline. Born in Nairobi, this karani
> does exactly that for your job hunt: reads every posting, files every
> application pack, tracks every outcome — and hands the pen back to
> you for the signature.

## Install

```bash
uv tool install karani     # or: pip install karani
karani init                # interactive setup -> karani.toml
karani config check        # see the resolved configuration
karani hunt                # schedule the hourly hunt
```

Hunting different roles is a config edit, not a code edit: `karani.toml`
owns what to hunt (roles, seniority, skills, comp shapes, relocation
destinations, target companies, your positioning) and which LLM provider
runs each task — API keys stay in `.env`. `karani refilter` re-judges
stored roles after any change.

- **Quickstart (from source):** below. **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md).
  **Planned work:** [docs/roadmap.md](docs/roadmap.md) (Tier 0 = good
  first issues). **Decisions:** `docs/adrs/`. **License:** MIT.
- Drive it from any MCP client (25 tools), the `karani` CLI (~30 verbs), or Slack.

## Positioning (the shipped defaults — yours lives in karani.toml)

Two role shapes qualify out of the box:

1. **Companies that hire globally at SF pay bands, regardless of candidate location.**
2. **Roles that sponsor a visa + relocation** — EU and Japan preferred destinations; local top-of-market comp acceptable there.

Target roles: software engineering, research engineering, ML/AI. Computational-bio / bioinformatics roles are excluded by title.

- Hard gates: senior/staff engineering role, remote (not hybrid) *unless relocation is sponsored*, region-locked jobs vetoed *unless relocation is sponsored*, comp ≥ $160k where disclosed.
- Nice-to-have: explicit pay parity language, relocation support, retreat/travel budget, must-have skill overlap.
- Postings are scored 0–100 for ranking after they pass the hard gates.
- Changed the rules? `karani refilter` re-judges every stored row.

## Sources

**ATS (per-company slug):** Greenhouse, Lever, Ashby, Workable
**Global feeds:** RemoteOK (tag-scoped), Himalayas (category-scoped), Remotive (category-scoped), We Work Remotely (already programming-only)
**Domain-specific:** aijobs.net (AI/ML board)

## Layout

One installable package: `karani/` — `cli.py` (the `karani` command),
`config/` (karani.toml), `karani/ingestion/` (deterministic tier),
`karani/qualification/` (LLM tier + providers), `karani/drafting/` (pack factory:
draft → humanize → tailor), `karani/intel/`, `karani/memory/`, `karani/slackbridge/`,
`karani/notionsync/`, `karani/autopilot/`, `karani/orchestration/` (LangGraph),
`karani/artifacts/` (MinIO), `karani/mcp_server/`. Full tree and rules: `CLAUDE.md`;
decisions: `docs/adrs/0001-0015`.

## Pipeline stages

1. **Fetch** — per-host semaphores (default 3), global concurrency 6, tenacity-backed retries on 5xx/429. Fetch errors surface per source.
2. **Classify** — deterministic `RoleCategory` (SWE, ML_AI, DATA, DEVOPS_SRE, SECURITY, RESEARCH, ...) + `Seniority`. Runs on title, then tags, then a description sample.
3. **Pre-filter** — word-boundary signal match on geo, remote, pay parity, comp anchored to currency keywords, skill overlap against the user profile. Hard-fails collected as `reasons_failed`.
4. **Cross-source dedup** — same `canonical_hash` (company + normalized title + posted-week) is suppressed within a run.
5. **Upsert** — Postgres, batched via `asyncio.gather` on a bounded semaphore. `active=TRUE`, `closed_at=NULL` on every touch.
6. **Sweep** — jobs not seen for `stale_job_days` (default 10) get `active=FALSE, closed_at=NOW()`. Prevents applying to dead reqs.
7. **Qualify** (separate command) — top-scored pending rows go to an LLM with your full resume + hints. Default provider is **OpenRouter** with `moonshotai/kimi-k2-thinking` at `reasoning_effort=high`. Returns `fit_score` (0–100), `verdict` (qualified|maybe|skip), evidence-backed strengths, gaps with mitigations, red flags, why-apply, and recommended positioning. Idempotent per resume hash — change your resume and re-qualify.
8. **Deliver** — autopilot posts full application packs to Slack as review cards; digest + worklist summaries push twice daily; the Notion board mirrors every state change.
9. **Feedback** — `verdict` command records your reaction (`apply|shortlist|later|skip|applied`) so downstream tuning has ground truth.

## Configure

```bash
karani init                                   # writes karani.toml (what to hunt, which models)
cp .env.example .env                          # secrets only: API keys, tokens, DSN
cp data/resume.md.example data/resume.md      # then edit it — this is YOU

# --- ingest + rank ---
karani run                    # fetch + pre-filter + sweep + discover
karani qualify --limit 50     # single-turn qualify
karani qualify --agent --limit 5   # tool-using agent (top-tier only)

# --- act on the shortlist ---
karani digest --format html --output data/digest.html
karani draft 12345            # cover letter + bullets + Q&A → drafts/*.md
karani verdict 12345 apply    # taste signal for future qualify runs

# --- application state machine ---
karani status 12345 applied
karani stage 12345 recruiter_screen --notes "30-min chat"
karani outcome 12345 offer

# --- housekeeping ---
karani discover               # probe unpromoted companies for ATS presence
karani sweep --days 14
karani stats
karani actions                # what to do next: review/draft/submit/follow up
karani funnel                 # response/interview/offer conversion rates
```

`karani hourly` runs one full LangGraph pass; `karani hunt` schedules it
hourly. Structure lives in `karani.toml` (see `karani.example.toml`);
env vars override any knob for one-off experiments.

## MCP server

The whole pipeline is exposed as an MCP server (stdio), so any MCP client —
Claude Code, Claude Desktop, Cowork — can drive the daily loop
conversationally:

```bash
karani mcp
```

### Connecting from your client

The server speaks stdio; every MCP host connects with the same two
tokens: command `karani`, args `["mcp"]`. It inherits the client's
environment — secrets come from your shell/.env, and `karani.toml` is
found in the working directory or `~/.karani/`.

**Claude Code** — the repo ships a project-scoped `.mcp.json`, so
sessions opened in this directory connect automatically. From anywhere
else (installed mode):

```bash
claude mcp add karani -- karani mcp
```

**Claude Desktop** — add to `claude_desktop_config.json`
(Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "karani": { "command": "karani", "args": ["mcp"] }
  }
}
```

**Cursor** — `.cursor/mcp.json` in your workspace (or the global one):

```json
{
  "mcpServers": {
    "karani": { "command": "karani", "args": ["mcp"] }
  }
}
```

**Any other MCP host** (VS Code + Copilot, Windsurf, Zed, your own
agent): configure a stdio server with command `karani`, args `["mcp"]`.
If the host can't resolve `karani` on PATH, use the absolute path from
`which karani`, or `uv` form: command `uv`, args
`["run", "karani", "mcp"]` with the repo as working directory.

### Using it

Once connected, you talk to your assistant normally — it picks the
tools. The conversations that earn their keep:

- *"What should I do on the job hunt today?"* → `next_actions` returns
  the prioritized worklist (review / draft / submit / follow-up, with
  fast-lane flags).
- *"Anything new worth applying to?"* → `shortlist`, then `get_job` for
  the one you ask about.
- *"Build the application for job 935."* → `draft` (tailored resume +
  humanized letter + artifact links), then *"mark it approved"* →
  `set_status`.
- *"I applied to the ClickHouse role through a referral."* →
  `set_status` with `warm_path=true` — feeds the warm-vs-cold funnel.
- *"They asked me about incident ownership in the screen."* →
  `record_question` — future prep packs for that company recall it.
- *"Remember that I won't consider crypto companies."* → `remember`;
  every later qualification recalls it.
- *"How is the hunt converting?"* → `funnel_stats`.
- *"Run a hunt pass now."* → `autopilot` (billed, budget-capped).

An orchestrating agent can run the whole loop headlessly:
`next_actions` → act → repeat. Billed tools (`qualify`, `draft`,
`prep`, `draft_followup`, `autopilot`) say so in their descriptions and
respect the same budget caps as the scheduler.

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
| `prep`, `draft_followup` | interview prep pack; dossier-hooked follow-up note (billed) |
| `company_intel`, `warm_paths` | cached public dossier; warm-path candidates |
| `notify_slack` | push digest or actions to Slack |
| `notion_sync` | reconcile the Notion job-hunt board |
| `autopilot` | one hunt pass: draft packs for top roles, deliver review cards |

Storage is shared across tool calls (Postgres via `DATABASE_URL`, or the
in-memory fallback for a scratch session). See
`docs/adrs/0008-mcp-server-interface.md` for the design.

## The continuous hunt (autopilot)

One command schedules the whole loop:

```bash
karani hunt
```

**Every hour** (a LangGraph pass — ADR 0013 — with per-node retry and
Slack alerts on failure): ingest all sources → qualify the new arrivals (idempotent
— already-qualified rows cost nothing) → **autopilot** drafts full
application packs for new top-fit roles and posts each to Slack as a
review card. Quiet by design: an hour with no new high-fit roles posts
nothing. Spend is triple-bounded — fit floor (`AUTOPILOT_MIN_FIT`, 85),
per-run cap (`AUTOPILOT_MAX_DRAFTS`, 3), and one shared daily budget
across all 24 runs (`AUTOPILOT_MAX_DRAFTS_PER_DAY`, 5). Summary pushes
(digest + worklist) stay twice daily (06:00, 13:00) so the channel isn't
spammed. Each card: summary, cover letter, and buttons —
*Approve pack* · *Skip role* · *I applied (warm)* · *I applied (cold)*.
Each pack now carries a *complete tailored resume* for the role plus the
cover letter, both stored as per-job objects in karani's MinIO with
presigned *tweak-and-submit* links on the card, and every pack passes a
humanizer (AI-tell detector + rewrite in your own voice; the deterministic
detector arbitrates, and the card shows the voice score). Approve marks it
`ready` and links the posting; you submit on the portal
and hit *I applied*. Every click records the verdict, feeds the
taste-calibration memory, and updates the Notion board. Karani never
submits an application — see ADR 0012.

Buttons require one extra toggle on the Slack app: **Interactivity &
Shortcuts → On** (no Request URL needed under Socket Mode).

Run a single pass manually with `karani autopilot`.

## Slack (two-way)

Karani pushes to Slack and takes commands back — full design in ADR 0010.

```bash
karani notify --kind digest    # push the shortlist
karani notify --kind actions   # push the worklist
karani slack        # two-way bridge (Socket Mode)
```

In the channel/DM, reply with the same verbs the CLI has: `actions`,
`digest`, `verdict 123 apply`, `status 123 applied`, `draft 123`,
`prep 123`, `followup 123`, `intel GitLab`, `warm GitLab`,
`remember <fact>`, `recall <query>`, `help`.

Setup: a Slack app with Socket Mode on (`SLACK_APP_TOKEN`), bot scopes
`chat:write` + `im:history` (`SLACK_BOT_TOKEN`), event subscription
`message.im`, and the target conversation id in `SLACK_CHANNEL`. Pushes
need only the bot token; the listener additionally needs
`uv sync --extra slack`.

## Conversion intelligence

The funnel is `application → response → screen → onsite → offer`; every
feature targets a stage (see roadmap Tier 1.5). `funnel` shows the rates
split by fit band, source, prompt version, warm-vs-cold, and posting age
at application, plus an autopsy (response rate by seniority/remote
status, keyword coverage responded-vs-silent). Fast-lane roles (fit >=
85, posted <= 3 days) are flagged in `actions` — apply same-day. Drafts
get a deterministic ATS keyword pass (`drafting/keywords.py`): JD terms
the resume misses feed the prompt, final coverage is persisted per
application. `warm <company>` ranks public engineers by overlap with
your skills; mark how you applied with `status <id> applied --warm` /
`--cold` so the warm-vs-cold split accumulates. `prep <id>` builds an
interview pack (company brief, gap-derived questions with STAR answers,
dossier-grounded questions to ask, warm-path openers); after each stage,
`asked <id> "<question>"` banks what they actually asked — future preps
for that company recall it. `followup <id>` drafts a note hooked on a
fresh company fact; `intel <company>` shows the cached dossier behind
all of it.

## Notion board

The job hunt mirrors onto a Notion database — one page per tracked
application, updated live on every verdict/status/outcome change and
reconciled by the scheduled run (ADR 0011; one-way, Postgres stays the
source of truth):

```bash
# one-time: create an internal integration at notion.so/my-integrations,
# share a parent page with it, put NOTION_TOKEN in .env, then:
karani notion init <parent_page_id>   # prints NOTION_DATABASE_ID
karani notion sync                    # full reconcile any time
```

Slack `sync` and the `notion_sync` MCP tool do the same reconcile.

## Scheduling

```bash
karani hunt        # installs two launchd agents (macOS)
karani unschedule
```

`com.karani.hourly` runs the hunt pass every hour; `com.karani.daily`
pushes digest + worklist summaries at 06:00 and 13:00. Every delivery
step is best-effort — an unconfigured or briefly-down channel never
sinks a pipeline run. Logs land in `logs/` (repo mode) or
`~/.karani/logs/` (installed mode).

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
karani remember "PostHog's screen asked about incident ownership" --kind question --company PostHog
karani recall "PostHog interview" --limit 5
```

## Infrastructure

Dedicated, disposable, local:

```bash
karani infra up      # Postgres + pgvector on localhost:5433
karani infra up --profile local-llm  # + Ollama on localhost:11434 (local LLM + memory extraction)
docker exec -it karani-db psql -U karani  # shell into the DB
karani infra down    # stop (volumes persist)
```

Point `DATABASE_URL` at `postgresql://karani:karani@localhost:5433/karani`
or keep Neon — the DSN is the only switch.

## LLM providers

Every LLM task (qualify, draft, humanize, tailor, prep, followup) is
independently routable in `karani.toml` `[llm.*]` — run bulk
qualification on a local model and keep a strong hosted model for
drafting. Env vars and `--provider`/`--model` flags override per run.

**OpenRouter (default).** Any OpenRouter model slug works — the current default is `moonshotai/kimi-k2-thinking` because Kimi K2 has strong long-context reasoning and OpenRouter exposes extended thinking via the standard `reasoning.effort` param. To swap models:

```bash
QUAL_PROVIDER=openrouter QUAL_MODEL=moonshotai/kimi-k2-thinking \
  karani qualify --limit 20
# or one-shot:
karani qualify --provider openrouter --model anthropic/claude-sonnet-4.5
```

Uses only `httpx` — no extra SDK needed. Reasoning tokens count toward completion; the default 16k token cap allows for it.

**Anthropic direct.** Install with `uv sync --extra anthropic`, then:

```bash
QUAL_PROVIDER=anthropic QUAL_MODEL=claude-haiku-4-5-20251001 \
  karani qualify --limit 50
```

**Local (zero token cost).** Any OpenAI-compatible server — Ollama,
LM Studio, vLLM, llama.cpp. No API key. Agent mode works with local models
that support tool calling (qwen3, llama3.3):

```bash
QUAL_PROVIDER=local LOCAL_LLM_MODEL=qwen3:32b \
  karani qualify --limit 50
# mix and match: cheap local bulk qualification, strong hosted drafting
karani draft 12345 --provider openrouter
```

Recommended split: local for bulk qualification (high volume, forgiving),
hosted for drafting (low volume, and draft quality is what gets the
interview).

**Agent mode** (`karani qualify --agent`) hands the qualifier tools —
web search, page fetch, GitHub org, Wikipedia — so it gathers evidence
about a company before ruling. 5–10x the cost per row; use it on top
candidates, not the daily batch (ADR 0007).

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

Every reaction — a `verdict` command, a Slack button click — does three
things at once: updates the state machine, becomes a few-shot
`[job → reaction]` example in the next qualification prompt, and writes
a distilled fact to the memory layer (recalled semantically per company
when mem0 is active). The system's taste converges on yours without any
retraining — and `karani funnel` measures whether it's working.

## Adding a source

1. Subclass `Fetcher` in `karani/ingestion/<source>.py`.
2. Register in `karani/ingestion/__init__.py` `FETCHERS` and add the enum to `Source`.
3. If it's a per-company ATS, add company slugs to `TARGETS`. If it's a feed, add the enum to `FEED_SOURCES`.
4. Every fetcher must use `get_with_retry` (per-host semaphore + backoff).
5. Every fetched job must call `.finalize()` so `content_hash` and `canonical_hash` are populated.

## Changing the rules

Edit `karani.toml` — roles, skills, shapes, exclusions, positioning —
then re-judge everything already stored (signal *phrases* stay in
`karani/ingestion/config.py`; extending them is a PR, per CLAUDE.md 4.2):

```bash
karani refilter   # re-runs the pre-filter over all active rows
```

Newly-passing rows queue for the next qualify run automatically.

## Gotchas

- **RemoteOK / Himalayas / Remotive schemas drift.** `raw` is stored on every row — write a reparser when the parser evolves.
- **Ashby comp** comes in two shapes. Both handled; adds via `to_usd` for non-USD currencies.
- **Workable** requires a per-posting detail fetch; we only detail-fetch titles that pass a title regex to keep the request count sane.
- **Slugs go stale.** Per-source outcomes print in the CLI so 404s surface immediately.
- **RemoteOK salary currency** is often missing. We only accept undeclared salaries when they fall in a plausible USD range; anything outside that is treated as undisclosed.
- **Pay parity** is a positive signal, not a hard gate. Companies rarely state it in the job post itself; look at the score column, not `pay_parity`, for ranking.
