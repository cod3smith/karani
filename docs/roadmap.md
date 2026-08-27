# Roadmap

Prioritized. Each item has (1) motivation, (2) concrete steps, (3) acceptance
criteria, (4) rough effort. Sequence is deliberate — earlier items unblock
later ones.

## Tier 1 — Complete the daily loop

### 1.1 Rotate the Neon DSN

**Motivation:** the original DSN was in `config.py` before the refactor.
Treat as leaked.

**Steps:**
1. Log into Neon → rotate password on the `neondb` role.
2. Update `.env` locally.
3. Confirm `python -m ingestion.cli stats` still runs.

**Acceptance:** old password no longer works; new one does.

**Effort:** 10 minutes.

### 1.2 Schedule daily runs

**Motivation:** `make daily` exists but nothing invokes it.

**Steps:**
1. On mac: `launchd` plist that runs `make daily` from the repo dir at
   06:00 EAT weekdays.
2. Also register a scheduled task via the Cowork `mcp__scheduled-tasks`
   MCP if we want the digest as a daily notification.
3. Log to `logs/karani-<date>.log`; add `logs/` to `.gitignore`.

**Acceptance:**
- `launchctl list | grep karani` shows the job.
- Log file appears after next fire time.
- HTML digest updates without manual intervention.

**Effort:** 1–2 hours.

### 1.3 Digest delivery channel

**Motivation:** HTML file on disk isn't a delivery surface. Kelyn needs
to *see* it without opening a file manager.

**Partially addressed (2026-08):** the MCP server (`mcp_server/`, ADR 0008)
exposes `digest` / `shortlist` / `get_job` to any MCP client, so the daily
review can happen inside Claude Code or Cowork without touching the HTML
file. A push channel (email/Slack/artifact) is still open.

**Options (pick one):**
- **Email via SES/Sendgrid.** Simple, reliable, works offline.
- **Cowork artifact.** Persistent, refreshable, ties into Cowork's
  daily surface.
- **Slack DM to self.** Works if Kelyn already lives in Slack.

**Recommendation:** Cowork artifact first (highest fit with how Kelyn
already works), email as fallback.

**Steps:**
1. `ingestion/digest.py` already renders HTML.
2. Wire `cli.py digest --deliver cowork` to call
   `mcp__cowork__create_artifact` with the rendered HTML.
3. On subsequent runs, `mcp__cowork__update_artifact` instead of creating
   a new one (dedupe by artifact ID stored in `data/.digest_artifact_id`).

**Acceptance:** running `make digest` refreshes a persistent Cowork
artifact that Kelyn can pin.

**Effort:** half-day.

## Tier 1.5 — Conversion intelligence

The funnel is `application → response → screen → onsite → offer`, and the
bottleneck is response rate (cold applications convert at 1–3%). Everything
in this tier targets a named funnel metric, measured via `funnel_stats`.

### 1.5.1 Funnel instrumentation — SHIPPED (2026-08)

`Storage.funnel_stats()` + the `funnel` CLI verb + `funnel_stats` MCP tool:
response/interview/offer rates overall and split by fit band, source, and
qualify/draft prompt version. Draft provenance (`draft_prompt_version`,
`draft_model`) is persisted per application via `Storage.record_draft`, so
draft prompts are A/B-testable against response rate. This is the
measurement layer every item below reports into.

### 1.5.2 next_actions worklist — SHIPPED (2026-08)

`Storage.next_actions()` + `actions` CLI verb + `next_actions` MCP tool.
Buckets: review (fit + freshness ranked), to_draft, to_submit, follow_up
(applied >= N days, no response). An orchestrating agent's loop is now
"call next_actions, act, repeat".

### 1.5.3 Warm-path finder

**Motivation:** referrals/direct contact convert 5–10x better than portal
submissions. Biggest single lever on response rate.

**Steps:**
1. Extend the `github_org` agent tool into `find_warm_paths(company)`:
   engineers with public GitHub/blog/talk presence, overlap-scored against
   Kelyn's domains (data platforms, causal ML).
2. `warm_paths` JSONB on the job row; drafting emits an outreach note per
   contact alongside the cover letter.
3. Karani drafts; Kelyn sends. No automated outreach — see non-goals.

**Acceptance:** >= 50% of drafted applications include at least one named
warm contact + note; `funnel_stats` gains a warm-vs-cold response split.

**Effort:** 1–2 days.

### 1.5.4 Freshness urgency

**Motivation:** response odds decay hard with posting age; recruiters
triage the first days of applicants.

**Steps:**
1. `next_actions.review` already ranks by fit then freshness. Add a
   `fast_lane: true` flag for fit >= 85 and posted <= 3 days.
2. Digest surfaces fast-lane roles in a "apply today" section.

**Acceptance:** response rate by posting-age-at-application visible in
`funnel_stats` after ~20 applications; fast-lane flag in digest + tool.

**Effort:** 2 hours.

### 1.5.5 Keyword-gap scoring on drafts

**Motivation:** an ATS ranks the application before a human reads it.
Deterministic, free, and turns "tailored" into a number.

**Steps:**
1. `drafting/keywords.py` — term overlap between JD and (resume + tailored
   bullets), stopword-filtered, word-boundary matched (same rules as the
   pre-filter). Coverage score 0–1.
2. Gap list feeds the drafting prompt; score persisted next to
   `draft_prompt_version`.

**Acceptance:** every draft has a coverage score; `funnel_stats` can
correlate coverage vs response rate.

**Effort:** half-day.

### 1.5.6 Interview prep pack + question bank

**Motivation:** once response rate rises, screen → onsite conversion is
the next bottleneck. The qualifier's evidence-backed `gaps` are exactly
what interviewers probe — nobody's gap analysis feeds their prep.

**Steps:**
1. `prep(job_id)` (drafting mode + MCP tool): company brief from a cached
   `company_intel` table (funding, news, eng blog, GitHub); anticipated
   questions generated from stored `gaps` with STAR answers from the
   resume; 3–5 questions to ask derived from company data (blog posts,
   launches, incidents) — never generic.
2. Structure the `stages` notes: after each stage, log questions actually
   asked. Per-company/per-stage question bank compounds into future preps.

**Acceptance:** prep pack generated for a job in `screen`; questions cite
company-specific sources; screen → next-stage rate tracked before/after.

**Effort:** 2 days (includes `company_intel` table, reused by 2.3 and 3.1).

### 1.5.7 Rejection autopsy

**Motivation:** ghosts and rejections carry structure (seniority band,
comp tier, timezone, company stage) that should feed back into filtering
and positioning.

**Steps:** quarterly-style report over outcome rows: common attributes of
ghosted vs responded, rendered by `funnel` CLI verb; findings become
pre-filter/positioning adjustments by hand first, automated later.

**Acceptance:** ghost-rate trend over rolling 30 applications visible;
at least one actionable pattern surfaced from real data.

**Effort:** half-day.

### 1.5.8 Follow-up sequencing

**Motivation:** a specific, newsworthy follow-up resurrects a real
fraction of silent applications. `next_actions.follow_up` already surfaces
who is due.

**Steps:** `draft_followup(job_id)` — day-7/day-14 notes referencing
something new from `company_intel`. Karani drafts; Kelyn sends.

**Acceptance:** follow-up drafts generated for due rows; response rate on
followed-up vs silent tracked in `funnel_stats`.

**Effort:** half-day (after 1.5.6's `company_intel` exists).

### 1.5.9 Self-improvement loop (measure → select → promote)

**Motivation:** "learning" at this data volume is few-shot memory plus
selection pressure, not fine-tuning (see non-goals). The mechanisms:

- **Episodic memory:** `user_verdict` few-shot pairs (exists), weighted by
  recency + outcome (5.1/5.2).
- **Semantic memory:** `company_intel` (1.5.6) and the question bank.
- **Calibration:** `funnel_stats` by fit band IS the calibration report —
  if fit-90 roles respond at the same rate as fit-70, the qualifier is
  miscalibrated; bump `PROMPT_VERSION` and compare bands across versions.
- **Selection:** prompt versions are genotypes, response rate per version
  is fitness. Promote the winner by hand; keep the loser's rows for
  comparison. No auto-rewriting prompts — human promotes.
- **Active learning (later):** when the qualifier's verdict is `maybe`
  with fit 60–75 (max uncertainty), surface it for a user verdict first —
  those labels teach the few-shot memory the most per label.

**Acceptance:** after any prompt bump, `funnel_stats` cleanly splits the
old vs new version's response rates; a documented promote/rollback ritual
in `docs/operations.md`.

**Effort:** mostly shipped by 1.5.1; the ritual doc + active-learning
filter is a half-day.

## Tier 2 — Improve signal quality

### 2.1 Per-source health metrics table

**Motivation:** right now `RunStats.per_source` is ephemeral. If Himalayas
starts returning empty responses, we won't notice for weeks.

**Steps:**
1. New table: `source_runs (source, slug, run_at, fetched, errors,
   error_msgs JSONB, duration_ms)`.
2. `orchestrator.py:run` persists one row per (source, slug) per run.
3. `cli.py stats --sources` prints last-24h and last-7d totals per source.
4. Cheap CI: alert if `fetched=0` for 3 consecutive runs on a given
   source.

**Acceptance:** `stats --sources` shows the table; test asserts a row
is written.

**Effort:** half-day.

### 2.2 Cross-source dedup persistence

**Motivation:** current dedup happens *within a single orchestrator run*
via `canonical_hash`. Across runs, the same job on different sources
enters the DB twice.

**Steps:**
1. Change `UPSERT` in `storage.py` to key on `canonical_hash` in a
   secondary INSERT-ON-CONFLICT clause.
2. Or: after upsert, run a "merge duplicates" pass that promotes the
   ATS entry over the feed entry and marks the other as `merged_into`.
3. Prefer approach 2 — preserves history of *where* we saw it.

**Acceptance:** two feeds surfacing the same GitLab role produce a
single qualified row.

**Effort:** day.

### 2.3 Levels.fyi comp overlay

**Motivation:** ~50% of JDs don't disclose comp. `fit_score` currently
takes a hit on all of them. If we can backfill even a rough band from
Levels/Payscale, scoring improves for the entire "opaque" tier.

**Steps:**
1. Add `qualification/comp_lookup.py` — new agent tool
   `lookup_levels_fyi(company)` that (a) hits Levels' public search
   HTML, (b) parses top 3 recent bands for the target level.
2. Cache in `company_comp_intel` table with a 14-day TTL.
3. When comp isn't disclosed in the JD, feed the lookup result into the
   qualifier prompt as `<comp_intel>` block.
4. This *replaces* the current hard-fail on missing comp with a soft
   penalty backed by intel.

**Acceptance:** three sample jobs with no JD comp get a
`comp_intel` block populated; fit scores shift accordingly.

**Effort:** day.

## Tier 3 — Agent expansion

### 3.1 More tools, better tools

Current tools: `web_search`, `fetch_url`, `github_org`, `wikipedia_summary`.

**Add:**
- `check_engineering_blog(company)` — probe common paths
  (`/engineering`, `/blog`, `engineering.{domain}`), return last 3 post
  titles + dates. Signals culture + shipping cadence.
- `fetch_recent_news(company)` — one-year window from web search,
  focus on funding / layoffs / product launches. Uses `web_search`
  under the hood but with a specialized query template.
- `check_glassdoor_rating(company)` — free tier scrapes rating + N reviews.
  Fragile; try, fall back gracefully.

**Steps:** each tool = one `Tool(...)` entry + a smoke test.

**Acceptance:** agent-mode qualify surfaces at least one instance of
each tool being called usefully on real data.

**Effort:** half-day per tool.

### 3.2 Structured tool-call log per row

**Motivation:** `evidence_gathered` is a list of strings today. For
debugging / cost tracking / prompt tuning, we want the raw
`{tool, args, result_bytes, latency_ms}` structure.

**Steps:**
1. Extend `QualificationResult.evidence_gathered` to a list of dicts.
2. Backfill schema note in `docs/adrs/`.

**Acceptance:** stored `qualification` JSONB has structured evidence
you can query.

**Effort:** 2 hours.

## Tier 4 — Discovery

### 4.1 Auto-promote hit companies to TARGETS.py

**Motivation:** `discovered_companies` table exists but `TARGETS` is
still a hand-curated Python list. Promoted rows should flow into the
next run automatically.

**Steps:**
1. In `orchestrator.py:run`, load promoted rows from
   `discovered_companies WHERE promoted_at IS NOT NULL` and append to
   the `TARGETS` iteration.
2. Split `Target` sources between static (`targets.py`) and dynamic
   (from DB).
3. Add a "promotion rules" gate — promote only if a role at that company
   scored `verdict=qualified` at least once.

**Acceptance:** a company discovered on RemoteOK with a qualified role
appears in the next `run` as a per-slug ATS fetch.

**Effort:** half-day.

### 4.2 YC Work-at-a-Startup scraper

**Motivation:** YC bio and AI companies are exactly Kelyn's intersection.
No JSON API but stable-ish HTML.

**Steps:**
1. `ingestion/ycwaas.py` — fetch `https://www.workatastartup.com/companies?query=...`
   pages, parse into `Job` objects.
2. Add to `FEED_SOURCES`.
3. Consider auth requirement — YC WAAS gates some content.

**Acceptance:** at least 5 YC bio/AI roles appear in `run` output.

**Effort:** day.

## Tier 5 — Feedback loop hardening

### 5.1 Weight past_verdicts by recency + confidence

**Motivation:** `recent_user_verdicts` currently returns raw pairs in
recency order. Better: weight recent + confident (Kelyn wrote `apply`
followed by `applied`) higher than a stale `later`.

**Steps:**
1. Change SQL to return a weighted score, not just a list.
2. Filter to <90 days.
3. Cap examples to 10 in the prompt (avoid token bloat).

**Acceptance:** measurable shift in fit scores on the same job after
adding 5 relevant verdicts.

**Effort:** 2 hours.

### 5.2 Outcome-weighted taste calibration

**Motivation:** `user_verdict=apply` is a soft signal; `outcome=offer`
is a hard one. Weight the latter heavily.

**Steps:**
1. Extend `recent_user_verdicts` to also pull `outcome` and `stages`.
2. Prompt block renders as "user applied → got to interview" vs
   "user applied → ghosted."

**Acceptance:** after 5 outcome rows exist, subsequent qualifications
lean toward companies with positive outcomes and away from ghosters.

**Effort:** half-day.

## Tier 6 — Nice-to-haves (deferred, low ROI)

- Docker image for reproducible daily runs.
- Backup Neon → S3 snapshot weekly.
- A tiny web dashboard (Streamlit) for verdicting from a phone.
- A "reject wall" — companies to never suggest again (Kelyn's exes,
  competitors of DataQRL).
- Rate limit dashboard for RemoteOK / GitHub / OpenRouter.
- Multi-user support — explicit non-goal, don't build it.

## What's explicitly NOT on the roadmap

- **Auto-submit applications.** See `docs/vision.md` non-goals.
- **LinkedIn / Indeed integration.** Same.
- **A generic "career copilot" product.** Same.
- **Fine-tuning a model on Kelyn's verdicts.** Few-shot is enough at
  this data volume; fine-tuning is the wrong tool.
