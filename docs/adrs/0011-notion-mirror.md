# 0011 · Notion board as a karani-owned mirror, not a second store

**Status:** accepted

## Context

Kelyn wants the job hunt visible on a Notion board that stays current
without manual updates. Two architectures were possible: (a) an
orchestrating agent (ADAM/Claude) syncs conversationally via a Notion
MCP connector, or (b) karani talks to the Notion API directly with its
own integration token.

## Decision

(b) — `notionsync/`: a thin httpx client (same pattern as the Slack and
OpenRouter clients, no SDK) plus sync logic. Rationale: "constantly
updated" requires updates from cron and from every state change, with no
human or agent in the loop.

Shape of the mirror:

1. **Postgres remains the system of record; Notion is a projection.**
   Sync is one-way (karani → Notion). Edits made on the board are not
   read back — the board is for viewing and sharing, verbs go through
   Slack/CLI/MCP. This avoids two-way conflict resolution entirely.
2. **Page identity lives on the job row** (`notion_page_id`): create
   once, PATCH thereafter, no Notion queries needed. A page deleted on
   the Notion side 404s on PATCH and is transparently recreated.
3. **Karani owns the database schema.** `notion init <parent_page_id>`
   creates the board (status/verdict selects, fit, applied date,
   outcome, warm-path checkbox, keyword coverage, apply link, job id).
4. **Two sync paths:** `maybe_sync_job` fires best-effort after every
   state change (verdict/status/outcome/draft on Slack and MCP) — it
   no-ops when unconfigured and never raises, so a Notion outage can't
   fail a verdict. The full `sync_jobs` pass runs in the scheduled
   `daily-full` chain and reconciles anything missed.
5. **Tracked set** = any job with a `user_verdict` or an
   `application_status` — the board shows the hunt, not the firehose of
   ingested postings.

## Consequences

- **Positive:** the board updates in real time from Slack replies and on
  schedule from cron; zero manual upkeep.
- **Positive:** no slack in the dependency budget — httpx only, and the
  feature is entirely optional (two env vars).
- **Negative:** one-way sync means board edits are cosmetic. Accepted by
  design; revisit only if Kelyn actually starts editing there.
- **Negative:** schema changes require re-running `notion init` (new
  board) or hand-editing properties. Acceptable at one-user scale.
