# 0001 · Tiered filter (deterministic → LLM → agent) instead of one LLM pass

**Status:** accepted

## Context

The simplest architecture would be: fetch every posting, hand each to an LLM,
ask "does Kelyn fit?" Simplicity has a cost model: at 10k postings/week and
$0.01/qualification, that's $100/week for mostly negative verdicts on roles
Kelyn was never going to apply to (sales, US-only, junior).

## Decision

Three tiers:

1. **Deterministic pre-filter** (`ingestion/filters.py`) — free, fast, drops
   ~95% of postings. Runs on every row.
2. **LLM single-turn qualification** (`qualification/qualify_one`) — ~$0.01/
   row, runs on ~5% survivors, produces structured JSON verdict.
3. **LLM agent-mode qualification** (`qualification/qualify_one_agent`) —
   ~$0.05–$0.15/row with tools, runs on-demand for top-scored candidates.

## Consequences

- **Positive:** Predictable cost. 90% of the signal from a deterministic
  filter with no LLM dependency; LLM budget spent on the interesting rows.
- **Positive:** Debuggable failures. When the pipeline drops something it
  shouldn't, the reason lands in `PreFilterResult.reasons_failed` — no LLM
  interpretation.
- **Negative:** More surface area. Three prompts (single, agent, draft) plus
  a signal catalog. Regression tests are essential.
- **Negative:** Signal changes require code changes. If we discover
  "location-independent pay" is now spelled a new way, that's a config edit.
  We accept this because the alternative (an LLM configuring the LLM) is a
  much bigger regression risk.
