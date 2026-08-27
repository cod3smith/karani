# 0005 · Application state machine as columns, not a separate table

**Status:** accepted

## Context

The application lifecycle has states (`new`, `drafting`, `applied`, `screen`,
`interview`, `offer`, `rejected`, ...) plus a per-stage log (recruiter
screen, technical, onsite, ...) plus a final outcome. There are two obvious
schemas:

**A) Columns on `jobs`:** `application_status`, `applied_at`, `stages` JSONB,
`outcome`, `outcome_at`, `draft_path`.

**B) Normalized tables:** `applications` (1:1 with `jobs`),
`application_stages` (1:N with applications).

## Decision

Option A. Columns on `jobs`. `stages` is a JSONB array of
`{stage, notes, at}` objects.

## Consequences

- **Positive:** Single table means all queries are simple. The digest, the
  stats output, the top-qualified query — all `SELECT ... FROM jobs`.
- **Positive:** No JOIN complexity for CLI paths. `stats --sources` is one
  query, `digest` is one query.
- **Positive:** Zero migration surface for the common state changes.
- **Negative:** Stage-level queries are awkward. "How many days between
  applied and offer?" requires JSONB unpacking. Acceptable — this is a
  personal tool, we're not building a candidate analytics dashboard.
- **Negative:** If we ever add per-stage rich data (recruiter name,
  interviewer notes, take-home artifacts), JSONB gets unwieldy. Escape
  hatch: promote `stages` to a real table then, with the same JSONB migrated
  via a script.

## Explicitly rejected

- **Full normalized schema.** Overkill for a single-user tool with <100
  applications/quarter.
- **Event sourcing.** Same — no audit or replay requirement warrants it.
- **State machine library (like `transitions`).** The rules fit in a
  `frozenset` + a switch in `set_application_status`; adding a library is
  a net negative.
