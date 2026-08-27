# 0014 · Per-job artifacts in object storage + a measured humanizer pass

**Status:** accepted

## Context

Two gaps between "pack card in Slack" and "application submitted":

1. The pack contained tailored *bullets* but not a full tailored resume —
   Kelyn still had to hand-edit the master resume per role, which is the
   slowest step of any application.
2. LLM-drafted prose carries recognizable AI tells ("I am excited",
   "leverage", em-dash cadence). Recruiters see hundreds of these; the
   voice is a real response-rate risk.

And the materials lived only as files on one laptop's `drafts/` dir —
nothing a phone could open from the Slack card.

## Decision

Three pieces, composed into one pipeline (`drafting/pipeline.py`) that
every surface (CLI `draft`, MCP `draft`, autopilot) calls — the flow can
never drift between surfaces:

    draft -> humanize -> tailor full resume -> upload artifacts -> record

1. **Full tailored resume per job** (`resume_tailor.py`, resume-v1):
   master resume + JD + qualification + keyword targets -> a complete
   reshaped resume markdown. Same facts, different emphasis; inventing
   experience is forbidden. Keyword coverage is scored on the output.
2. **Humanizer** (`humanize.py`, humanize-v1): a deterministic AI-tell
   detector (banned-phrase list, em-dash density, "not only...but also")
   plus an LLM rewrite in Kelyn's own voice (his resume is the style
   sample). The detector ARBITRATES: if the rewrite scores worse than
   the original, the original ships — we never pay for a worse draft.
   Before/after voice scores ride on the pack card. Anti-tell rules are
   baked into the resume-tailor prompt instead of running a second pass
   over the resume (one call, not two).
3. **Artifact store** (`artifacts/`, MinIO/S3 via env): one object
   prefix per job (`{id}-{company}/resume.md`, `cover_letter_pack.md`),
   presigned links (7d) on the Slack card — click, tweak, submit from
   anywhere. Karani runs its OWN MinIO in compose on :9010 (the DataQRL
   stack owns :9000 on this machine); `MINIO_ENDPOINT` points anywhere
   S3-compatible. Best-effort throughout: unconfigured or unreachable
   storage degrades to files-on-disk, never fails a draft.

Cost shape: a pack is now up to three LLM calls (draft, humanize,
tailor). `KARANI_HUMANIZE` and `KARANI_TAILOR_RESUME` each toggle one
off; autopilot's per-run and daily budgets bound the total regardless.

## Consequences

- **Positive:** "tweak and submit" becomes literal — every pack carries
  a complete role-specific resume and letter behind two links.
- **Positive:** voice quality is measured, not vibes: the tell detector
  gives every pack a before/after score, and `funnel_stats` can later
  correlate voice scores with response rates.
- **Negative:** ~3x LLM cost per pack. Bounded by autopilot budgets and
  per-flag toggleable; qualification (the volume path) is untouched.
- **Negative:** presigned URLs expire (7 days) and the local MinIO is
  laptop-bound. Acceptable for a single-user tool; re-run `draft` to
  refresh links.
