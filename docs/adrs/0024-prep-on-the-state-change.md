# ADR 0024 — Interview prep fires on the state change

Date: 2026-10-27
Status: accepted
Implements: career-runner design 5.5 (Phase 4)
Builds on: 0014 (artifacts), 0017 (per-task routing), 0019 (budget and
the cached prefix), 0020 (documents)

## Context

`karani prep <id>` has existed since the Tier 1.5 work and has been
run when the candidate thought of it. That is the wrong moment. The
useful moment is when an interview gets scheduled — days before it
happens, while there is still time to read a dossier, rehearse a story
and decide what to ask. Prep requested the night before is a
formality; prep delivered on the transition is preparation.

The pack itself was also one LLM call handed a resume and a dossier.
Everything it produced was therefore generated, including two things
that must not be: the experiences the candidate claims, and the
salary numbers they will repeat out loud.

Meanwhile Phase 1 built the thing that makes a good pack possible.
`karani/profile/` holds the career as structured records with stable
ids, and `data/experience.md` holds the candidate talking through nine
years in their own words. A resume is compressed to the point of being
generic; the transcript is where the decision that made a system work
still exists. That is STAR material, and it is real.

## Decision

**The trigger hangs off the state machine, not off a caller.**
`Storage.set_application_status` dispatches to
`karani.coach.notify_status_change` on every transition, and the coach
acts on `screen` and `interview`. Prep therefore arrives whatever
moved the row — CLI, MCP tool, Slack button, or the email watcher
reading a scheduling mail — and no caller has to know the coach
exists. The hook is a lazy import inside a swallowed exception: the
state machine is the system of record, and it must not go down for a
listener.

`applied` deliberately does not trigger. Nothing is scheduled yet, and
prepping every submitted application would spend the month's
allocation in a day.

**Three properties hold the trigger safe.** It is idempotent on the
stages log, because statuses get re-set — a Slack button, then the
watcher reading the same thread — and a second pass must not buy a
second pack. It records its marker even when the composing call
failed, so an unattended retry cannot spend the calls again;
`karani prep` stays the deliberate retry. And it is killable with
`KARANI_AUTO_PREP=off` (karani.toml `[coach] auto_prep`), because an
unattended billed call needs a switch within reach. The test suite
sets that switch off for the same reason it sets `KARANI_INTEL=off`.

**STAR stories are mined, not written.** The model returns records
citing where each claim came from and karani verifies the citation
before anything reaches the pack, exactly as the tailorer does for
resume bullets. Two checkable kinds: `source_ids` into the profile
core, dropped when the id does not exist; and `transcript_quote`,
which must be a verbatim span of the transcript under whitespace and
case normalization. A paraphrase fails, which is the point — the model
cannot launder an invention through a plausible-sounding quote. A
story left with neither is discarded rather than repaired, and a
question that cites a story verification deleted keeps the question
and loses the citation.

**Comp is derived.** The posted band, the declared floor and target,
and a benchmark over comparable postings already in the pipeline —
arithmetic, handed to the model as resolved facts it may phrase around
but never originate. Same rule as `karani/legal.py`, for the same
reason: these are numbers the candidate is accountable for. The
benchmark is advertised bands rather than a market survey and says so,
and under three comparables it refuses to show percentiles at all.

**The dossier is refreshed on a 3-day TTL** rather than the hunt's 14.
Three weeks of company news is exactly what an interviewer expects you
to have read.

**Two calls, one cached prefix.** The candidate block — resume, merged
profile, indexed records, transcript — goes in the system prompt and
is byte-identical between both calls and across jobs (ADR 0019). It is
the largest stable block karani owns, and the failure mode of getting
it wrong is silent: no error, just full price on every request.

## Consequences

- Moving a job to `screen` or `interview` is now a billed action
  costing two Sonnet calls. That is a real behavior change for anyone
  scripting status writes, and the kill switch is the answer.
- `drafting/prep.py` is deleted. prep-v3 supersedes prep-v2; packs
  persisted under either stay distinguishable by `prompt_version`.
- A pack whose composing call fails still ships its research — the
  dossier, the verified stories and the comp brief never needed a
  model. Half a pack beats none.
- Stories can come back empty when the transcript and profile are thin.
  That is the intended failure: the pack says nothing rather than
  inventing something the candidate has to defend under questioning.
- karani still owns no calendar. The interview date comes from an ISO
  date in the stage note, written by whoever recorded the stage, and
  the card says "date not recorded" when there is none.
