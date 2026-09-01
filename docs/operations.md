# Operations

Everything you need to actually *run* karani day-to-day.

## Environment variables

Full list; see `.env.example` for the template.

### Required

| Var | What it's for |
|---|---|
| `DATABASE_URL` | Neon Postgres DSN, `postgresql://...?sslmode=require` |
| `OPENROUTER_API_KEY` | LLM provider key (default) |

### Optional — LLM

| Var | Default | Notes |
|---|---|---|
| `QUAL_PROVIDER` | `openrouter` | `openrouter` or `anthropic` |
| `QUAL_MODEL` | `moonshotai/kimi-k2-thinking` | Any OpenRouter slug |
| `QUAL_REASONING_EFFORT` | `high` | `low`, `medium`, `high` |
| `QUAL_MAX_TOKENS` | `8000` | Cap on completion (incl. reasoning) |
| `QUAL_TIMEOUT_SECONDS` | `180` | Thinking models are slow |
| `RESUME_PATH` | `data/resume.md` | Where the resume lives |
| `ANTHROPIC_API_KEY` | *(unset)* | Only when `QUAL_PROVIDER=anthropic` |

### Optional — tuning

| Var | Default | Notes |
|---|---|---|
| `MIN_COMP_USD` | `160000` | Hard-fail comp floor |
| `TARGET_COMP_USD` | `220000` | Score bonus threshold |
| `STALE_JOB_DAYS` | `10` | Days since last-seen before auto-close |
| `HTTP_TIMEOUT` | `30` | Per-request seconds |
| `HTTP_CONCURRENCY` | `6` | Global cap |
| `HTTP_PER_HOST_CONCURRENCY` | `3` | Per-host cap |
| `CACHE_TTL_HOURS` | `4` | Reserved for future in-memory cache |
| `USER_AGENT` | `karani/0.2 (...)` | Sent on every HTTP request |
| `OPENROUTER_APP_NAME` | `karani` | Sent as X-Title header |
| `OPENROUTER_APP_URL` | `https://github.com/kelyn/karani` | Sent as HTTP-Referer |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Override for testing |

## First-time setup

```bash
cd karani
uv sync
uv sync --extra dev            # + pytest, ruff
uv sync --extra anthropic      # only if you want Anthropic direct

cp .env.example .env
# → fill DATABASE_URL and OPENROUTER_API_KEY

cp data/resume.md.example data/resume.md
# → replace with your real resume

# smoke
karani stats
karani run
karani qualify --limit 5
karani digest --format html --output data/digest.html
```

## Daily loop (recommended cron)

Nairobi timezone (EAT / UTC+3):

```
# Twice a day — pull new postings
30 5,13 * * *  cd /path/to/karani && make ingest       >> logs/ingest.log 2>&1

# Once a day, after morning ingest — qualify + digest
0  6    * * *  cd /path/to/karani && make daily-digest >> logs/qualify.log 2>&1

# Weekly — sweep stale + prune promoted-companies list
0  4 * * 0     cd /path/to/karani && make sweep        >> logs/sweep.log 2>&1
```

## Launchd (mac) equivalent

`~/Library/LaunchAgents/dev.karani.daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.karani.daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>cd /Users/theforge/Mirror/karani && make daily &gt;&gt; logs/daily.log 2&gt;&amp;1</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>6</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>/Users/theforge/Mirror/karani/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>/Users/theforge/Mirror/karani/logs/launchd.err</string>
</dict></plist>
```

Load: `launchctl load ~/Library/LaunchAgents/dev.karani.daily.plist`

## Local LLM (Ollama)

The reference config routes `[llm.qualify]` to Ollama (ADR 0017).
Operational notes:

- **macOS: run Ollama.app natively, never the compose container.**
  Docker on Mac has no GPU passthrough — qwen3:4b measured ~30-60x
  slower CPU-only in the VM. The compose `local-llm` profile is for
  Linux hosts with `--gpus`. Both bind 11434; stop one before starting
  the other.
- **Raise the context window.** Ollama defaults to a 4096-token
  context and *silently truncates* longer prompts — a qualification
  prompt (resume + posting + few-shot verdicts) is bigger than that.
  macOS app: `launchctl setenv OLLAMA_CONTEXT_LENGTH 16384`, then
  fully restart the app (`killall Ollama ollama` — the menu-bar quit
  can leave the server running). Verify after one request:
  `curl -s localhost:11434/api/ps` → `context_length`.
- **Pin one parallel slot.** With `OLLAMA_NUM_PARALLEL` > 1 the
  context window is split per slot, quietly undoing the point above:
  `launchctl setenv OLLAMA_NUM_PARALLEL 1`. Concurrent qualify workers
  then queue server-side, which is fine.
- **No JSON grammar mode.** `LocalQualifier` deliberately omits
  `response_format=json_object` — Ollama implements it as
  grammar-constrained decoding, which fights thinking models
  (measured ~60x slowdown on qwen3). Don't add it back.

## Secrets rotation

**Rotate every 90 days** for hygiene, immediately on any exposure:

1. **Neon DSN password.** Neon dashboard → roles → rotate. Update
   `.env`. Test: `karani stats`.
2. **OpenRouter API key.** OpenRouter dashboard → keys → new key +
   revoke old. Update `.env`. Test: run a single-row `qualify`.
3. **Anthropic key** (if using). Same drill on the Anthropic console.

Nothing else needs rotation — no cloud provider keys, no OAuth tokens,
no long-lived DB service accounts.

## Monitoring & alerting

Minimal but essential. Add these before daily automation:

### 1. Non-empty run assertion

If `run` fetches < 10 jobs across all sources, something's wrong (a
schema drifted, a slug 404'd, or the network's blocked). Add to cron
wrapper:

```bash
if make ingest 2>&1 | tee -a logs/ingest.log | grep -q 'fetched=0 '; then
    echo "karani: zero-fetch alert" | mail -s "karani broken" you@example.com
fi
```

### 2. LLM cost breach

Check OpenRouter dashboard weekly. If daily average > $2, something's
runaway — likely a broken idempotency check causing re-qualifications.

### 3. DB size

Neon free tier is generous but not unlimited. Alert at 80%:

```sql
SELECT pg_size_pretty(pg_database_size(current_database()));
```

If growth is unexpected, likely culprit: `raw` JSONB on `jobs`. Prune
old rows:

```sql
DELETE FROM jobs
 WHERE active = FALSE
   AND closed_at < NOW() - INTERVAL '90 days'
   AND application_status IS NULL;
```

## Backup

**Weekly Neon snapshot** to S3 (or Neon's built-in point-in-time
recovery). Manual for now — automate if `docs/roadmap.md` §6 gets
prioritized.

```bash
pg_dump "$DATABASE_URL" | gzip > backups/karani-$(date +%F).sql.gz
# copy to S3 or Backblaze B2
```

## Cost model

Ballpark, per day of active use:

| Item | Cost |
|---|---|
| Neon DB (free tier) | $0 |
| OpenRouter — qualify (50 rows single-turn, Kimi K2 Thinking) | ~$0.50 |
| OpenRouter — agent (5 rows tool-loop) | ~$0.50 |
| OpenRouter — draft (2 drafts/day) | ~$0.10 |
| **Total per day** | **~$1.10** |
| **Per month** | **~$33** |

Well below "not worth it" and well below the labor cost of manual
searching. If you're seeing multiples of this, check for:
- Idempotency drift (are we re-qualifying rows we already qualified?)
- Runaway agent loops (are `max_iterations` and `max_tool_calls`
  being respected?)
- Reasoning-effort creep (did someone set `QUAL_REASONING_EFFORT=high`
  on the batch path where `low` would do?)

## Debugging

### "Zero jobs fetched from source X"

1. Check the endpoint by hand: `curl <FEED_URL>`.
2. Check `raw` column of a recent row from that source — schema might
   have drifted.
3. Check per-host semaphore lockup — restart the process.

### "Qualification is returning verdict=maybe for everything"

Almost always a resume/prompt disconnect:
1. Print the actual prompt: uncomment a `log.info` in
   `qualification/client.py:qualify_one`.
2. Confirm the past-verdicts block isn't dominating.
3. Try a different model via `--model anthropic/claude-sonnet-4.5`
   to isolate model vs prompt.

### "Draft is generic / off-voice"

1. Read `data/resume.md` — is it actually up to date?
2. Read the qualification for that job — is `recommended_positioning`
   real signal or generic?
3. Bump `DRAFT_PROMPT_VERSION` and rewrite the voice rules in the
   system prompt.

### "DB migration failed"

Never. Every schema change is `ADD COLUMN IF NOT EXISTS`. If you see
a migration failure, someone broke the rule — revert.

## Common ops recipes

```bash
# Re-qualify everything against an updated resume
# (already automatic on resume_hash change, but you can force):
psql "$DATABASE_URL" -c "UPDATE jobs SET qualification_resume_hash = NULL WHERE prefilter_passed = TRUE;"
make qualify

# Retire a company (never suggest again)
psql "$DATABASE_URL" -c "UPDATE jobs SET active = FALSE, closed_at = NOW() WHERE company_display = 'X';"

# Export shortlist to CSV
psql "$DATABASE_URL" -F, -A -c "SELECT id, company_display, title, fit_score, apply_url FROM jobs WHERE verdict = 'qualified' AND active = TRUE ORDER BY fit_score DESC;" > shortlist.csv

# Restart from a clean slate (nuclear)
psql "$DATABASE_URL" -c "TRUNCATE jobs, discovered_companies;"
make ingest qualify digest
```
