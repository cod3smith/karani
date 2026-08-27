# 0004 · Normalized content hash + canonical hash for dedup

**Status:** accepted

## Context

The original `content_hash` was `sha256(title + description_text)` on raw
bytes. Two problems:

1. **Cosmetic edits triggered re-qualification.** A recruiter fixing a typo
   or reformatting a bullet changed the hash → LLM re-qualified → billed.
2. **No cross-source dedup.** GitLab posts to Greenhouse, then it appears
   on Himalayas and RemoteOK. Same job, three different content hashes,
   three qualifications billed.

## Decision

Two hashes on every `Job`:

1. **`content_hash`** — `sha256(normalized_title | normalized_description)`
   where "normalized" = lowercased, punctuation-stripped, whitespace-collapsed.
   Used for change detection *within* a source. Cosmetic edits don't trigger
   re-qualification; substantive edits do.

2. **`canonical_hash`** — `sha256(normalized_company | normalized_title |
   posted_week)`. Used for cross-source dedup. Same job appearing on three
   sources produces the same canonical hash.

In-run dedup: the orchestrator suppresses duplicate canonical hashes before
upsert, preferring ATS (per-slug) rows over feed rows.

Cross-run dedup: currently NOT implemented — same canonical hash can enter
the DB twice across separate runs. Roadmap item 2.2.

## Consequences

- **Positive:** LLM cost drops meaningfully in practice — most JD "changes"
  are cosmetic.
- **Positive:** Cross-source noise disappears within a run.
- **Negative:** Normalization loses signal. Two roles with the same
  normalized title at the same company in the same week collapse — even if
  one is Backend and one is Frontend. Mitigation: the qualification LLM
  distinguishes them when it re-runs. In practice this is a rare edge case.
- **Negative:** Migration surface. Both hashes must be recomputed if the
  normalization function changes. `Job.finalize()` centralizes this.
