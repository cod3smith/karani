# 0002 · Positioning: SF-band global remote, not Kenya-specific

**Status:** accepted (supersedes an implicit v0.1 positioning)

## Context

The first version of the pipeline filtered for "Kenya-eligible" roles. The
signal set was named `kenya_positive_signals` / `kenya_negative_signals`.
This produced two failure modes:

1. **False positives on "global" as a marketing word.** Any JD with "global
   brand" or "worldwide team" fired the positive signal. Most such roles are
   region-locked.
2. **False negatives on genuine global-hire roles** that happened not to
   mention Kenya or Africa. GitLab / Automattic / Deel roles are the target
   segment, and they don't say "we hire from Kenya" — they say "we hire from
   anywhere."

## Decision

Reposition the pipeline around **companies that hire globally at SF pay
bands regardless of candidate location**. Kelyn's location becomes an
implicit downstream consequence of the "hire globally" filter, not an
explicit filter input.

Concrete changes:

- Rename signals to `global_hire_positive_signals`,
  `regional_restriction_signals`, `pay_parity_signals`.
- Comp floor $140k → $160k (below that is not SF band).
- Add explicit "pay parity" signals (location-independent pay language) as
  a positive score contributor.

## Consequences

- **Positive:** the pipeline now finds roles Kelyn's manual search was
  missing (small companies with strong bands, no African visibility).
- **Positive:** aligns with the market thesis. The addressable segment
  isn't "Kenya-friendly companies" — it's "location-agnostic-pay companies
  who happen to permit Kenya."
- **Negative:** Africa-native companies (Flutterwave, M-KOPA) drop in
  scoring because their bands are regional, not SF. Mitigation: they're
  still in `TARGETS` for optionality but score lower.
- **Negative:** any documentation referring to "Kenya-remote" needed to
  change. Handled in this refactor.
