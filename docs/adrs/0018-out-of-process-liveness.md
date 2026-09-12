# ADR 0018 — Liveness must live outside the process it watches

Date: 2026-09-12
Status: accepted
Extends: 0016 (run ledger + heartbeat), 0013 (LangGraph orchestration)

## Context

karani ran nothing for eight days and said nothing about it.

On 2026-09-04 a routine disk cleanup (`find ~/Mirror -type d -name
.venv | xargs rm -rf`) deleted the project virtualenv. Both launchd
agents hardcode `.venv/bin/karani`, so every hourly tick exited 127
("command not found") straight into a log file nobody reads. The
outage surfaced only when the operator noticed no Slack traffic.

ADR 0016 anticipated a dead scheduler and added `heartbeat_alert()`:
if the newest `hourly` ledger row is older than three hours, post to
Slack. It never fired, and could not have. The heartbeat runs inside
`karani notify`, launched by the *daily* agent — which executes the
same `.venv/bin/karani` binary as the hourly hunt. One deleted
directory took out the watcher and the watched together.

That is a common-mode failure: two components that look independent
(separate launchd jobs, separate schedules, separate code paths) share
a hidden dependency, so a single fault disables both. Splitting the
*schedule* was never enough; the runtime was still shared.

## Decision

**Liveness checking moves out of the Python process entirely.**

1. A completed pass timestamps a marker file (`logs/.last-pass`,
   overridable via `KARANI_PASS_MARKER`). Written after the graph
   returns, in `run_hunt_once`, and never allowed to raise — same rule
   as the run ledger. It answers exactly one question: *did a pass
   finish?* Row-level failures stay the report node's job (ADR 0016).

2. `com.karani.watchdog` reads that marker and nothing else. It is
   POSIX `sh` plus `curl`, installed to `~/.karani/watchdog.sh`,
   outside the repository. It imports no karani code, activates no
   virtualenv, opens no database connection, and runs under
   `/bin/sh` — an interpreter that ships with the OS. If the marker is
   missing or older than `KARANI_WATCHDOG_MAX_AGE_H` (default 4), it
   posts to Slack directly over the web API. A test asserts no
   executable line in the script references `.venv`, `uv run`,
   `python`, or the `karani` binary; that assertion is the ADR.

3. The scheduler no longer runs from the repo's `.venv`. `karani hunt`
   bakes in whatever `karani` resolves on PATH, so the supported
   install is `uv tool install --editable`, whose environment lives in
   `~/.local/share/uv/tools/` — not a directory that cleanup scripts
   target by name.

4. Every path baked into a launchd job is absolute. launchd supplies no
   working directory, so the relative `logs` that repo mode yields made
   it refuse to spawn the job (EX_CONFIG, exit 78) and pointed the
   marker somewhere unresolvable. Caught by a test that forces repo
   mode; the installed layout was already absolute and hid the bug.

The in-process heartbeat stays. It catches what it is good at — the
hourly job unloaded, crashing on startup, or wedged while the rest of
karani still works — and it costs nothing.

## Consequences

- The alarm survives deletion of the venv, a broken interpreter, an
  unparseable config, and a dependency that fails to import. It does
  not survive the Mac being off, the Slack token being revoked, or
  launchd itself being unloaded. Those need an off-box check
  (healthchecks.io or similar); deliberately not taken on yet, because
  it adds an external dependency and an account to the setup.
- Two alerts can fire for one outage (in-process heartbeat at 3h,
  watchdog at 4h). Duplicate warnings are cheap; a missed outage cost
  eight days.
- The watchdog holds a copy of the Slack channel id and reads the bot
  token from `.env` at run time. The token is never written into the
  script — same rule as ADR 0015 (secrets in env, never in config).
- `uv tool install --editable` tracks source edits, but a dependency
  change needs a reinstall. Documented in `docs/operations.md`.
