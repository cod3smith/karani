# 0006 · In-memory storage fallback as a real code path

**Status:** accepted

## Context

Every storage method has an `if self.pool is None: ...` branch that
implements the same operation against an in-memory `dict[key, row]`. This
adds ~30% to `storage.py` LoC. There's a tempting shortcut: raise an
exception on any operation when the DB is down.

## Decision

Treat the in-memory fallback as a **first-class code path**, not a
degraded/emergency mode.

## Rationale

1. **Tests need it.** The pytest suite runs the same code paths as production
   without a Postgres dependency. This is worth the LoC alone.
2. **Local dev needs it.** Kelyn can iterate on filter changes / prompt
   changes / classifier changes without ever pointing at Neon.
3. **CI needs it.** Deterministic tests + no Postgres service in CI.
4. **Signal at boot time.** `storage.connect()` logs a warning when the
   fallback kicks in but doesn't fail. That means the CLI is usable for
   maintenance operations (rendering docs, running tests) even when the
   DSN is wrong.

## Consequences

- **Positive:** All the above. The test suite is 58 tests, 0.22s, no
  service dependency.
- **Positive:** Refactors are safe. Every storage change gets exercised
  through the fallback in tests before it ever touches Postgres.
- **Negative:** Two implementations to keep in sync. Every new method adds
  a memory branch. Regression risk: someone adds a method to the pool
  branch only. Mitigation: tests must cover both paths.
- **Negative:** The fallback is not persistent across process restarts.
  Users need to know this — surfaced via the boot-time warning log.

## Non-consequence

- **Feature parity between paths.** No: the fallback does not implement
  `discovered_companies` operations (they're best-effort no-ops in memory
  because auto-promotion is a Postgres-only workflow). Documented in the
  method docstrings.
