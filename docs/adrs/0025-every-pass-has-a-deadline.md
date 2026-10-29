# ADR 0025 — Every pass has a deadline

Date: 2026-10-29
Status: accepted
Extends: 0018 (out-of-process liveness), 0013 (LangGraph pass)

## Context

karani went silent for 40 hours while every health indicator looked
fine. `launchctl list` showed the hourly job present and exit code 0.
The database was up, MinIO was up, the binary resolved, the budget had
$37 of $40 left. The scheduler was not dead — it was *running*. One
pass, started on 27 October, had blocked on a request to localhost
Ollama that never returned, and launchd will not start a second
instance of a job while the first is alive. A single wedged call
silenced the entire pipeline.

The proximate cause: Ollama auto-updated from 0.33.3 to 0.40.11 and
restarted mid-request. The client socket stayed ESTABLISHED and the
caller — the mem0 memory layer reaching for embeddings — had no
timeout, so it waited. It would have waited forever.

The watchdog (ADR 0018) worked exactly as designed, alerting every
hour that no pass had completed. It could do nothing else, because
reporting is all it can do. That is the right scope for a dead-man's
switch, and it is why detection is not sufficient on its own.

Two failure modes now have precedent here: a pass that cannot *start*
(the deleted virtualenv, ADR 0018) and a pass that cannot *finish*.
Only the first was defended.

## Decision

**A hunt pass runs under a hard deadline** — `run_with_deadline` in
`orchestration/graph.py`, default 3000s (50 minutes), configurable via
`KARANI_PASS_DEADLINE_SECONDS` and clamped below the hourly tick. A
pass that exceeds it is abandoned, logged, and reported as a failed
run with a non-zero exit so the launchd record shows the lost hour
rather than a silent success. The next tick starts clean.

The ceiling lives at the pass boundary, not on each client. Which call
hangs is unpredictable — this one was mem0's embedder, the next could
be an ATS, a Slack post or a Notion sync — and a per-client timeout
only protects the client someone remembered to configure. One ceiling
covers every call, present and future.

A clamp keeps the deadline under an hour on purpose: a 60-minute
ceiling on an hourly job still lets a wedged pass eat its successor's
slot, which is the failure this exists to prevent.

## Consequences

- A hang now costs one pass, not the pipeline. Worst case is 50
  minutes of lost work, and the hourly cadence survives.
- A legitimately slow pass is now killable. The local-model path was
  already measured at 75-170s per row and the batch is clamped to 15
  rows for exactly that reason (ADR 0019); a hosted pass runs in
  ~12 minutes. 50 minutes leaves substantial headroom, but a future
  change that makes passes genuinely slower must raise the deadline
  rather than discover it.
- Partial work survives: the graph's nodes commit as they go, so an
  abandoned pass keeps whatever it had already written.
- This does not fix mem0's missing timeout, which remains worth
  setting. It makes that fix non-urgent rather than load-bearing.
- Unaddressed: a pass killed at the deadline still holds whatever
  advisory lock it took until the connection drops. Postgres releases
  session-scoped locks when the session ends, and the best-effort
  unlock added earlier tolerates a dead session, so this self-heals —
  but it is the thing to check first if a pass reports "another run
  holds the lock" straight after a deadline kill.
