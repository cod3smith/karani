# 0012 · Autopilot drafts, human sends

**Status:** accepted

## Context

Kelyn wants the pipeline to hunt continuously: find matching roles, build
the application pack, and deliver it for review — not wait for him to ask
per job. That moves billed drafting from human-triggered to scheduled,
and it brushes against the hardest non-goal in `docs/vision.md`:
karani never submits an application.

## Decision

An `autopilot/` pass in the scheduled chain: draft packs for top
candidates and post each to Slack as a review card with buttons —
**Approve pack** (→ verdict `apply`, status `ready`), **Skip role**
(→ verdict `skip`), **I applied (warm/cold)** (→ status `applied` +
warm-path flag). Buttons arrive over the same Socket Mode connection
(`interactive` envelopes); each click maps onto the exact same Storage
transitions as the text verbs — routing only, no new state logic.

Autonomy guardrails, in order of importance:

1. **Never submits.** "Approve" marks the pack `ready`; the card links
   the posting and the human applies on the portal. No button, tool, or
   code path submits anything anywhere.
2. **Fit floor** (`AUTOPILOT_MIN_FIT`, default 85): only roles the
   qualifier is confident about earn an unattended billed draft.
3. **Double-bounded spend.** Per-run cap (`AUTOPILOT_MAX_DRAFTS`,
   default 3; 0 disables) AND a shared daily budget
   (`AUTOPILOT_MAX_DRAFTS_PER_DAY`, default 5, gated on `drafted_at`).
   The daily budget is what makes hourly scheduling safe: 24 runs share
   one ceiling, they don't multiply it. Amended 2026-08 when the
   schedule moved from twice-daily to hourly (summary pushes stay
   twice-daily to avoid channel spam).
4. **No double billing:** drafting moves the job to `drafting`, which
   removes it from the candidate pool; a failed draft stays eligible and
   retries next pass.
5. **Review is the verdict.** Every button click feeds the same
   taste-calibration memory and Notion sync as a typed verdict — the
   feedback loop loses nothing to the convenience.

## Consequences

- **Positive:** the loop inverts — instead of Kelyn pulling a digest and
  asking for drafts, finished packs arrive and he clears a review queue
  from his phone. Time-to-apply on fast-lane roles drops from days to
  hours, which is the response-rate thesis (1.5.4) acted on.
- **Positive:** spend is bounded and tunable per env, and `verdict=skip`
  clicks teach the qualifier which "fit 85+" roles it overrates.
- **Negative:** a systematically miscalibrated qualifier burns draft
  budget on roles Kelyn skips. Mitigation: the cap bounds the cost and
  `funnel_stats` by fit band exposes the miscalibration.
- **Negative:** requires Interactivity enabled on the Slack app (no URL
  needed under Socket Mode). One-time toggle, documented in README.
