# ADR 0019 — Hosted Claude tiers behind a monthly allocation

Date: 2026-10-23
Status: accepted
Extends: 0017 (pluggable providers), 0015 (config), 0016 (verify gate)

## Context

Three facts collided.

The OpenRouter key died in September and was never replaced, so ADR
0017's fallback — local Ollama — became the only tier actually running.
qwen3:4b costs nothing but takes 75-170s per row, which turned the
"hourly" hunt into 9-13 hour passes and had the watchdog alarming every
hour against a threshold it could not meet.

The operator then obtained Claude API credits, shared with other
projects. "Has $100" is not "may spend $100": karani needs an explicit
slice, and a guard that holds it.

And the volume is far smaller than it looks. 11,795 postings ingested,
but the deterministic pre-filter (ADR 0002) drops ~95% before any LLM
call — 775 rows have ever passed it, 696 have been judged. The bulk
tier is ~400 calls a month, not 36,000. Costing it as if every posting
reached the model overstated the bill by two orders of magnitude.

## Decision

**Per-task routing to Claude**, within the ADR 0017 registry:

| Task | Model | Why |
|---|---|---|
| qualify | Haiku 5.5, effort `low` | ~400 calls/month; cheapest model, and the verdict is a short JSON object |
| draft, tailor | Opus 5.5 | ~30 packs/month and the pack is the artifact that gets the interview |
| agent (verify), prep, followup | Sonnet 5.5 | tool use and long-form prep, an order of magnitude below Opus |
| humanize | local llama3.2:3b | already scores 92->100 in production, for free |

**A stable/volatile prompt split** (`PROMPT_VERSION` -> qual-v5). Two
thirds of a qualification request — instructions, resume, taste
calibration — is identical for every row in a pass, but it sat in the
per-row user prompt where a prefix cache cannot reach it. It now
renders into the system prompt, and the Anthropic provider marks it
`cache_control: ephemeral`. Reads bill at ~0.1x, writes at 1.25x, so
the break-even is two requests and a 50-row pass is pure margin.

The marker is withheld below 512 tokens, the current models' minimum —
a shorter prefix silently caches nothing while still charging the 1.25x
write. This also rules Haiku 4.5 out of the bulk tier: its minimum is
4096 tokens, which karani's ~3k prefix would never clear.

**Every call is priced** (`karani/budget.py`) and the total rides in the
run ledger's existing `tokens` blob — no schema change, and rows from
before cost tracking read as zero. Cached input is priced separately
from plain input, because a cache read is discounted, not free.

**Past the cap, degrade — do not halt, do not overspend.** With the
allocation spent, or with no API key configured, the factory returns
the local model and records why; the hourly report announces it once
(a spent budget lasts until the month rolls over, so an hourly reminder
would only teach the channel to ignore karani). This is the same
degradation contract as the memory layer and the storage fallback.

**Plans follow the provider.** ADR 0017 matched fan-out to
`max_parallel`; batch size now follows `max_batch` the same way, so a
mid-month fallback to local does not turn a 50-row batch into a
two-hour pass.

## Consequences

- Expected spend is ~$7/month against a $15 cap: qualify $0.20,
  drafting ~$5, verify ~$1.50. The verify gate (ADR 0016), switched off
  because it was "billed", is now affordable and on.
- Qualification drops from 75-170s/row to seconds, so passes fit the
  hour again and the watchdog threshold becomes meaningful.
- qual-v4 and qual-v5 rows are different cohorts. Nothing re-qualifies
  (that keys on `content_hash`), but comparisons across the boundary
  must filter on `prompt_version`.
- The month-to-date figure counts scheduler passes only; one-off CLI
  and MCP calls are not in the ledger. The guard therefore sees a floor,
  which errs toward spending less than the cap, not more.
- Tests can no longer reach a billed endpoint: conftest strips
  ANTHROPIC / OPENROUTER / OPENAI keys. Two tests were silently relying
  on the developer's real OpenRouter key and now carry dummies.
