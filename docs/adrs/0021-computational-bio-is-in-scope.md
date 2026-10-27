# ADR 0021 — Computational biology is in scope; exclusions become liftable

Date: 2026-10-27
Status: accepted
Supersedes the comp-bio exclusion in ADR 0003 (pre-filter signals) and
the positioning in ADR 0015 / CLAUDE.md rule 4.1

## Context

karani has excluded computational-biology and bioinformatics roles
since the first pre-filter. The reasoning was sound at the time: the
candidate's NeoRx work is biomedical, the titles attract wet-lab and
lab-adjacent postings, and a hunt aimed at staff software engineering
did not want "Computational Biologist" filling the shortlist.

The exclusion was enforced in four places — built-in
`excluded_title_terms`, `[positioning] excluded_domains` rendered into
every prompt, the qualification rules, and CLAUDE.md. Config could
only ever *append* exclusions, so there was no way to lift one.

The candidate now wants research-engineering roles on both sides of
their background. That is not a reversal of judgement so much as a
correction: the NeoRx pipeline applied Pearl's do-calculus with
reinforcement learning to drug-resistance prediction. That is systems
and modelling work, and the labs hiring for it — Recursion, insitro,
Isomorphic, Deep Genomics — are hiring research engineers, not
bench scientists.

## Decision

1. **`[profile] allow_titles` lifts terms out of the built-in
   exclusions.** The defaults stay as they are: they encode a sensible
   generic software-engineering hunt, which is the right default for a
   project anyone can `pip install`. A candidate whose own background
   covers an excluded domain declares it, and the loader subtracts
   those terms before appending `exclude_titles`.

   One trap this surfaced: allowing `"computational biologist"` is not
   enough while `"biologist"` remains excluded, because the shorter
   term matches inside the longer title. The allowlist has to name
   every term that matches, and a test pins the real titles rather
   than the terms.

2. **Wet-lab terms stay excluded.** `medicinal chemist` and
   `chemistry` are not lifted: the thesis is building systems and
   models, not bench work. "Computational Biologist" now passes;
   "Medicinal Chemist" still drops.

3. **`excluded_domains` may be empty**, and the persona then omits the
   "roles in X are OUT" sentence entirely rather than rendering it
   against an empty string.

4. **A research-engineering target tier** (`targets.py` Tier 3b): AI
   labs and computational-biology outfits, on the ATSes karani already
   reads. Deliberately a curated target list and not a web scraper —
   these employers post to Greenhouse, Lever and Ashby, so scraping
   would fight bot detection for strictly worse data.

## Consequences

- The shortlist will include comp-bio research engineering. If it
  fills with wet-lab postings the allowlist is too broad — narrow it
  rather than restoring a blanket exclusion.
- Prompt versions move (qual-v7) because the persona text changes; old
  and new cohorts stay distinguishable by `prompt_version`.
- Any `pip install karani` user keeps the generic defaults, and now has
  a supported way to hunt in a domain the defaults exclude. The feature
  is general; this candidate is the first case.
- Rows already judged under the exclusion are not re-qualified
  (that keys on `content_hash`), so previously-dropped comp-bio
  postings only resurface when re-ingested or via `karani refilter`.
