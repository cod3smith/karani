# Vision

## The problem karani solves

Applying to senior/staff engineering roles from Nairobi is a signal-to-noise
problem, not a discovery problem. Job boards surface thousands of postings
per day; the intersection of *(a) fully remote, no-country-restriction* +
*(b) SF-band comp regardless of location* + *(c) role Kelyn is actually
qualified for* is < 1% of the raw volume. Manually filtering that intersection
takes 45 minutes a day and still misses most of it. Kelyn was applying to
five roles a week and getting maybe one screen for the effort.

karani inverts that: broad, cheap ingestion + tiered filtering + LLM
qualification against a real resume + drafted applications. The goal is
*apply to fewer, better-fit roles, with better tailoring, in less time.*

## The thesis

Two overlapping bets:

1. **Global-remote at SF bands is a real, growing market segment.** GitLab,
   Deel, Remote, Automattic, Buffer, PostHog, Hugging Face, Doist, Canonical
   — the list of companies that hire globally with published or transparent
   pay bands is small but shipping. If we can *reliably identify* these
   roles in the flood, the application funnel is dramatically better than
   the location-adjusted market.

2. **LLMs are cost-effective at the qualification tier, not the discovery
   tier.** Running a Kimi K2 Thinking call on every RemoteOK posting is
   dumb. Running one on the top 5–10 pre-filtered candidates per day is
   cheap enough (< $5 per pass) to be strategic. The whole pipeline is
   engineered around that tier split.

## Positioning axes

Every design decision in the codebase can be traced to one of these:

- **Global-remote vs region-locked.** Region-locked = veto. See
  `regional_restriction_signals` in `config.py`.
- **SF-band comp vs location-adjusted.** Location-adjusted, when detected
  via `pay_parity_signals`, drops the fit score but doesn't hard-veto (the
  signal is often absent from JDs). Below $160k where disclosed = veto.
- **Senior IC (staff/principal) vs junior/manager.** Junior = veto. Manager
  is allowed but scored slightly lower — Kelyn's next role should be
  technical.
- **Engineering-adjacent role vs everything else.** Sales, marketing,
  design, ops = veto at the role classifier tier. See
  `RoleCategory.OTHER` handling in `filters.py`.

## Who this is for

**Primary user: Kelyn Njeri** (Nairobi, senior/staff SWE, data-platform +
causal-ML background). karani is not a product; it's a personal tool. If
someone else wants to use it, they fork it and rewrite `data/resume.md`,
`ingestion/profile.py`, `docs/vision.md`, and probably `targets.py`.

Design implications:
- No multi-tenant assumptions in the DB schema. Single-user everywhere.
- No admin UI. CLI + HTML digest + generated markdown drafts.
- No user accounts, auth, or sharing. Local file + local DB.

## Non-goals

These are things karani deliberately does not do, so we don't waste effort:

1. **It's not a job board.** No public site, no discovery UX for other users.
2. **It's not an applicant-tracking system for recruiters.** State machine
   tracks Kelyn's applications, not a company's candidates.
3. **It won't submit applications autonomously.** Autonomy stops at "here's
   a drafted cover letter and a state-machine transition." Kelyn hits
   "Submit" himself. Reasons: legal (some applications have TOS about
   automated submission), trust (a bad LLM day = broken applications), and
   signal (the recruiter's read of a personal application is part of the
   value).
4. **It won't scrape LinkedIn or Indeed.** ToS-hostile, brittle. If
   Kelyn's targets show up on LinkedIn but nowhere else, we add them to
   `TARGETS` manually.
5. **It's not a general-purpose LLM harness.** Every prompt is
   career-specific and lives in a versioned prompts module.
6. **It doesn't try to be a "product" outside the workflow.** No metrics
   dashboards, no ChartJS, no Slack app. Terminal + HTML + markdown.

## Success metrics

Real ones, not vanity:

- **Time to shortlist**: minutes per day between "karani ran" and
  "Kelyn's decided what to apply to." Target: < 15 minutes.
- **Qualification precision**: fraction of `verdict=qualified` rows Kelyn
  agrees with. Target: > 60% after the feedback loop has 30+ pairs.
- **Application → screen conversion**: pre-karani baseline is ~1 in 5.
  Target: > 1 in 3 within 2 months of use.
- **LLM cost per applied role**: target < $2 (qualification + drafting +
  agent). Track via OpenRouter dashboard.

## Non-metrics

- Total roles ingested. More sources ≠ more value.
- Total qualifications run. Cost per applied role is what matters.
- LLM eval scores. The user-verdict feedback loop is the eval.
