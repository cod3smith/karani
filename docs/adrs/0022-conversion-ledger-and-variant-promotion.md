# ADR 0022 — A conversion ledger, and promotion that refuses on noise

Date: 2026-10-27
Status: accepted
Implements phase 5 of `docs/superpowers/specs/2026-10-23-career-runner-design.md`
Builds on ADR 0005 (state machine as columns), 0012 (autopilot drafts,
humans send), 0017 (pluggable providers), 0019 (budget)

## Context

The design doc is blunt about what a learning loop over job
applications can and cannot do. It cannot influence a hiring
decision — headcount, internal candidates, timing and committee
politics are neither observable nor controllable. What it can
optimize is a short list: which roles are targeted, which positioning
angle the pack leads with, the reply rate, and the time to a first
response.

It is equally blunt about the constraint. A learning loop needs
labels, and karani has zero: 11,795 postings ingested, 121 packs
drafted, 0 applications sent. Every number this layer produces will,
for its first months, come from a sample small enough that the naive
reading of it is wrong.

Two failure modes follow, and they are the ones this ADR is written
against:

1. **Promoting on noise.** Three applications on one angle and three
   on another will, roughly half the time, show one angle "winning"
   3-0. Acting on that is worse than acting on nothing, because it
   retires an arm that was never tested.
2. **Comparing incomparable numbers.** This one already happened. A
   598-row qualification run in 2026-10 measured Haiku 5.5 at a mean
   fit of 38 with 2 rows above 80, qwen3:4b at 47 with 31 above 80,
   and Kimi at 51. The autopilot's fit floor had been calibrated
   against Kimi's scale and was silently matching almost nothing. A
   fit score is one model's opinion on that model's scale.

## Decision

### A separate `application_ledger` table, not more columns on `jobs`

An application is a historical fact: what was sent, under which
prompt version and model, with which documents, on a given day. The
`jobs` row is a mutable discovery index that gets re-upserted on
every ingest pass and whose `qualification` is recomputed whenever the
content hash moves. Provenance stored there drifts.

The table is created with `CREATE TABLE IF NOT EXISTS` and widened
with `ADD COLUMN IF NOT EXISTS` only (CLAUDE.md 4.7). The DDL lives in
`karani/analytics/ledger.py` and runs on first use rather than being
appended to `ingestion/storage.py:SCHEMA`, which keeps the merge
surface on that file at zero.

`sync()` reconciles the ledger from `jobs` — every row with an
`applied_at` gets an entry — so the ledger starts working without any
other surface being wired up. Facts that cannot be recovered by
reading `jobs` (the variant tag, a reply timestamp from the watcher)
are preserved across reconciliations.

`first_response_at` is tracked separately from `outcome_at`, and the
earliest reply wins. A screen that preceded an eventual rejection is
the first response, because speed and frequency of reply are the
controllables; the rejection is not.

### Strategy variants as rows, with uniform allocation

A variant is a named arm with a `guidance` text that defines the
treatment. It is a row rather than a config entry because in six weeks
you need to know what "outcome-first" meant when it was applied, not
what the file says then.

Assignment is `sorted(active_arms)[job_id % n]`: reproducible (a
re-draft does not silently switch treatment), balanced without
counters, and stateless. Thompson sampling or epsilon-greedy would
allocate more traffic to the leader, which at this sample size means
allocating more traffic to whichever arm got lucky first.

Applications with no variant tag are a bucket named `unassigned`, and
never an arm. Nothing was deliberately varied, so nothing can be
attributed, however many of them reply.

### Promotion needs a stated sample AND separated intervals

`promotion_report` returns one of four statuses: `no_data`,
`insufficient_data`, `no_clear_winner`, `promote`. It promotes only
when at least two arms have reached `MIN_SAMPLE_PER_ARM` (20)
applications each, and the leader's 95% Wilson interval does not
overlap any other eligible arm's.

Non-overlapping intervals is a stricter test than a two-proportion
z-test. That is deliberate: the cost of running an experiment one
more month is a month, and the cost of acting on noise is every
application after it. Every rate anywhere in this layer is reported
with its Wilson interval, which at n=3 is honestly enormous — the
width is the message.

When the answer is "not enough data", the report says so and names
the shortfall. `tests/test_analytics_stats.py` pins that a 3/3 arm
beside a 0/3 arm produces no winner.

### Every fit comparison is segmented by scorer

The pooled fit-band table is populated only when a single
`(qualify_model, qualify_prompt_version)` produced every score. With
more than one, it is empty and `scale_warning` names each scorer with
its mean. Each scale keeps its own bands and distribution.

The same argument applies to the experiment itself: if one arm was
drafted by Opus and the other by Sonnet, the arm is not what varied,
so the report emits a confound line naming the split rather than
letting a model difference be read as an angle difference.

### The coach review is derived, not generated

No LLM call. Every line is arithmetic over recorded facts, so it costs
nothing, cannot invent a trend, and says the same thing twice. A model
asked to explain four rejections will always find a reason.

It is built to be useful at n=0: with nothing sent, the output is one
observation and one action, which is the correct output.

## Consequences

- The ledger is additive and reversible. Dropping
  `application_ledger` loses attribution but nothing else; `sync()`
  rebuilds everything derivable from `jobs`.
- Variant tagging is explicit, so until something tags applications
  the experiment reports `insufficient_data` with "none tagged". This
  is the honest state, not a bug: an auto-assigned label on a pack
  whose text did not change would make the arms incomparable while
  looking like data.
- Guidance text is not yet threaded into the draft prompt. Doing so
  means editing `drafting/prompts.py` and bumping
  `DRAFT_PROMPT_VERSION`, which belongs with the drafting work rather
  than the measurement layer. `karani variant assign` surfaces the
  guidance to whoever produces the pack in the meantime, and
  `run_variant` warns when an arm has no guidance.
- `MIN_SAMPLE_PER_ARM = 20` over two arms at ~30 applications a month
  is roughly six weeks before promotion is even eligible, and the
  interval test will usually want longer. That is the design doc's
  own estimate, not a pessimistic reading of it.
- Reporting `offers` is bookkeeping, not an objective. Nothing in
  this layer optimizes toward it, and the promotion text says so
  explicitly: the winning arm gets more replies, which says nothing
  about who gets hired.
