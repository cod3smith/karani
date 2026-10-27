# 0023 · A static submission console, and a write-back that creates labels

**Status:** accepted
**Builds on:** 0012 (never submits), 0014 (artifacts), 0020 (documents)

## Context

The funnel read 11,795 ingested, 775 through the pre-filter, 127
qualified, 121 packs drafted, **0 applied**. Every stage worked except
the one that produces outcomes. Nothing upstream of submission can fix
that: better ingestion makes 121 larger, not non-zero.

Three mundane causes, none of them intelligence (career-runner design,
section 2). Format was solved by ADR 0020 — packs now render to
ATS-safe `.docx` and PDF. Two remained:

- **Assembly.** The answers exist, spread through a markdown document,
  a profile and a config block, while the form wants twelve discrete
  fields retyped per application.
- **State.** Nothing recorded a submission, so `applied` stayed 0, the
  follow-up logic never armed, and the learning loop had no labels.

## Decision

A `karani/submission/` package with four pieces, and one CLI verb at
each end of a session: `karani console`, then `karani submitted`.

1. **ATS question introspection** (`questions.py`). Greenhouse, Lever
   and Ashby publish the application form on the same public endpoints
   karani already ingests from, so the sheet answers *this* form, with
   the company's own wording and option values, not a generic guess.
   Coverage is uneven and the degradation is explicit: Greenhouse
   returns the full set from one extra GET (through `get_with_retry`,
   rule 4.5); Lever's custom question cards and Ashby's application
   form arrive inside the payload already stored in `jobs.raw`, which
   costs no request at all; anything that exposes nothing falls back to
   the fields every form asks, flagged on the card as a fallback. The
   learned set is cached in `jobs.ats_questions`, so the second pass is
   free. Network is opt-in (`KARANI_ATS_QUESTIONS`, off in tests).

2. **An answer sheet** (`answers.py`), deterministic, no LLM call. Each
   question is matched to a slot and answered from a named source, and
   the sources are not interchangeable:
   - work authorization, sponsorship, relocation and start date come
     from `karani/legal.py` — **always**, even when the drafted pack
     contains prose answering the same question. These are attestations
     the candidate signs; a model has no authoritative facts to answer
     from, and the same question has opposite correct answers by role
     shape (globally-remote through an EOR needs no visa; the Berlin
     on-site role does).
   - identity from the merged profile, documents from presigned
     artifact links, prose from the pack, comp from the posted band.
   - demographic self-identification is deliberately left blank.
   - anything with no source is flagged `needs_input` rather than
     invented: an empty field gets filled by the human looking at it, a
     plausible wrong one does not.

3. **A static console** (`console.py`): one self-contained HTML file,
   mobile-first, per job an apply link, document links and every answer
   one tap from the clipboard.

4. **Write-back** (`Storage.mark_submitted`): status `applied`, the
   timestamp, a `submitted` stage, and a *snapshot* of the artifact
   keys as they were at send time.

### Why a file and not a server

A server needs a port, a process, and a reason to be running when the
laptop sleeps. A file opens from iCloud or AirDrop on a phone, which is
where a review queue actually gets cleared. The cost is that the page
cannot write to Postgres: it keeps the ticks in `localStorage` and
hands back one `karani submitted 12 34 56` command. That is a real
seam — a tick that is never followed by the command is a lost label —
and it buys a page that is incapable of sending anything anywhere,
which is ADR 0012 enforced by construction rather than by policy.

### Why the artifact snapshot

`jobs.artifacts` is overwritten by the next draft. The audit question
is what the company actually received, so `submitted_artifacts` freezes
the keys at send time and later re-drafts cannot rewrite history.

## Consequences

- **Positive:** the per-application cost drops from "open the ATS and
  retype twelve fields" to scan, paste, attach, click. That is the
  whole bottleneck between 121 drafted and 0 sent.
- **Positive:** `applied` becomes non-zero with a timestamp, which arms
  follow-ups and gives the conversion ledger its first labels. Nothing
  in the learning loop (design section 4) is possible before this.
- **Positive:** the legal answers reach the form through exactly one
  code path, which is auditable and testable, and no model output can
  become an attestation.
- **Negative:** marking in the console and recording in the database
  are two steps, and the second can be skipped. Mitigated by the
  command being generated for you; measured by `actions`, where a pack
  that is still `ready` after a week is visible.
- **Negative:** presigned links expire (7 days, ADR 0014), so a console
  left open for a fortnight has dead document links. Re-run
  `karani console` — it is free.
- **Negative:** Greenhouse question introspection is one GET per queued
  job on the first pass. Bounded by the queue size and cached after.
