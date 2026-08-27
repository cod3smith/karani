# 0007 · Agent mode is opt-in, not default

**Status:** accepted

## Context

The tool-using agent loop (`qualify_one_agent`) is a strictly stronger
qualification path: it can look up comp data, check GitHub activity, and
read engineering blogs before ruling. It's also 5–10× more expensive per
row and 3–5× slower.

Should we make it the default?

## Decision

No. Single-turn (`qualify_one`) is the default for `qualify`; agent mode is
opt-in via `--agent`.

## Rationale

1. **The pre-filter already killed the low-signal rows.** By the time
   qualification runs, we're on ~5% of the raw ingestion. Most of these
   still don't need agent-level scrutiny — the JD text carries enough
   signal for a `qualified` / `maybe` / `skip` decision.
2. **Cost curves matter.** At the daily batch of ~50 pre-filtered rows,
   single-turn is $0.50; full agent is $5+. Kelyn saves the agent budget
   for the top 5–10 candidates.
3. **Agent tools depend on OpenRouter capability.** Only `OpenRouterQualifier`
   implements `chat_turn`. Making agent default would leak provider choice
   into every workflow.

## When to use agent mode

- **Top-of-list decisioning.** When single-turn says "qualified" and the
  fit_score is >85, agent mode adds evidence-backed confidence before
  Kelyn spends 45 minutes drafting.
- **Ambiguous cases.** Comp isn't disclosed, remote status is unclear —
  agent can look these up.
- **New companies.** Never seen them before, want context beyond the JD.

## When NOT to use agent mode

- **Batch qualification** of the day's inbox.
- **Anything where single-turn is decisive.** If the JD says "US only,"
  agent mode is going to reach the same conclusion at 10× cost.

## Consequences

- **Positive:** Cost control by default. Agent is a knob Kelyn turns when
  he needs it.
- **Positive:** Provider portability. Single-turn works on Anthropic too.
- **Negative:** Two code paths in the qualification runner. Mitigation:
  they share `qualify_pending` with an `agent_mode: bool`, so the surface
  area diff is small.
- **Negative:** Users might forget agent mode exists. Documented in
  `README.md`, `CLAUDE.md`, and the `--help` text.
