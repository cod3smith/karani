# karani as a career runner — design

Date: 2026-10-23
Status: draft for review
Builds on: ADR 0012 (autopilot), 0014 (artifacts), 0016 (verify), 0019 (budget)

## 1. The number that decides the priorities

```
ingested      11,795
passed filter    775
qualified        127
packs drafted    121
applied            0
```

Every stage works except the one that produces outcomes. karani is a
discovery-and-drafting engine feeding a cul-de-sac. Improving ingestion,
scoring or model quality makes 121 larger; it does not make it non-zero.

So the organizing principle of this design: **everything serves
submission throughput and the feedback it generates.** A feature that
does not help an application get sent, or help karani learn from one
that was, is out of scope for now.

## 2. Why submission is stuck

Three mundane causes, none of them intelligence:

1. **Format.** The pack is markdown. No ATS accepts markdown, so every
   application starts with manual conversion.
2. **Assembly.** The answers exist, spread through a document, while the
   form wants twelve discrete fields — name, authorization, salary,
   notice, "why us" — retyped per application.
3. **State.** Nothing records a submission, so `applied` stays 0, the
   follow-up logic never arms, and there is no outcome to learn from.

## 3. Goals and non-goals

**Goals.** Submission-ready documents in object storage. An hour-long
batch session that clears the queue. Every application auditable: what
was sent, which artifact version, when. Responses detected and routed.
Prep delivered before an interview, not requested after it. A measured
feedback loop from outcome back to strategy.

**Non-goals, deliberately.** karani never submits an application — the
rule from ADR 0012 stands. No LinkedIn scraping: the data export is the
supported path, and building bot-detection evasion is both against
their terms and a liability. No auto-replying to recruiters; drafts
only, for the same reason karani does not auto-submit. No ATS account
automation — fragile, and the answer sheet captures most of the value.

## 4. On "recursive self-improving agents"

Worth being precise, because the honest version is valuable and the
literal version is not.

What cannot be promised: that karani lands a role the moment you
submit. Hiring outcomes depend on headcount, internal candidates,
timing and committee politics — none observable, none controllable.
Any system claiming to convert submission into offer is selling
something.

What a learning loop genuinely optimizes is the controllables:

| Signal | Controllable? | Measurable from |
|---|---|---|
| Which roles are targeted | yes | prefilter + fit score vs. outcomes |
| Positioning angle in the pack | yes | `recommended_positioning` vs. reply rate |
| Response rate | partly | email watcher labels |
| Time-to-first-response | partly | ledger timestamps |
| Interview -> offer | weakly | stage transitions |
| The hiring decision | no | — |

And the hard constraint: **a learning loop needs labels, and karani has
zero.** With 0 submissions there is nothing to learn from; the first
thirty applications are what makes any of this possible. That is the
strongest argument for building the submission layer before anything
clever.

The implementable form of "self-improving" is an evaluation harness,
not an agent rewriting itself:

- **Strategy variants are versioned.** Positioning angles, draft prompt
  variants and resume orderings are named and recorded per application
  (`prompt_version` already is).
- **Outcomes attach to variants.** The email watcher labels each
  application: no response / rejection / screen / onsite / offer.
- **Promotion is measured, not vibed.** Once a variant has enough
  applications behind it, compare reply rates and promote the winner —
  a bandit over a handful of arms, not an open-ended agent. With ~30
  applications a month, expect meaningful signal in one to two months,
  and treat anything earlier as noise.
- **The coach surface reads the same ledger.** Weekly: what converted,
  which skills recur in roles that rejected you, which to close.

This is slower and less magical than "recursive self-improvement", and
it is the version that actually compounds.

## 5. Architecture

### 5.1 Profile core

One structured model as the source of truth — positions, projects,
skills, education, achievements — rather than resume prose. Sources:
`data/resume.md` (today), the LinkedIn **data export** (official
archive: positions, skills, recommendations), and a fetch of the
portfolio site. Tailoring then selects and reorders *data* instead of
rewriting *text*, which is both better and cheaper.

### 5.2 Document renderers

Two renderers over that model, MIT CAPD conventions: centered
name/contact header, Education before Experience, strict reverse
chronological, action-verb bullets with quantified outcomes, single
column, no tables or graphics, standard section headings, one page.

- `.docx` via python-docx — the ATS-safe artifact. No tables, text
  boxes, headers/footers or columns, because those are what parsers
  mangle.
- `.pdf` — same semantic model, for the human who opens it.

Named `Kelyn_Njeri_Resume_<Company>_<Role>.pdf`, versioned into the
existing MinIO per-job prefix (ADR 0014) with presigned links.

### 5.3 Submission console

One published HTML artifact, regenerated each pass, listing every
approved pack: apply link, document links, and a copy-button field
sheet. The leverage: Greenhouse, Lever and Ashby expose each job's
*actual* application questions through the same public APIs karani
already ingests from, so the sheet answers that job's real form rather
than a generic guess. Each row ends in "mark submitted", which writes
status, timestamp and artifact version back to Postgres and Notion.

That write-back is what moves `applied` off zero, arms follow-ups, and
creates the label the learning loop needs.

### 5.4 Response watcher

Gmail API, read-only scope. Classify inbound (rejection / screen /
assessment / scheduling / offer / recruiter outreach) on Haiku, match to
an application by ATS sender domain, company and thread, advance the
state machine, draft a reply for review. Never sends.

### 5.5 Prep on trigger

On transition to screen or onsite: refresh the company dossier
(`karani/intel/`), generate role-specific questions, mine STAR stories
from the profile core, add comp research and questions to ask —
delivered as a document plus a Slack card, ahead of the date.

### 5.6 Conversion ledger and coach

Applications carry positioning, model, prompt version, artifacts and
outcome. `funnel_stats` extends to answer: which angle converts, which
sources, which comp bands, whether the humanizer changes anything. The
coach surface is a weekly read over that ledger.

## 6. Phasing

| Phase | Deliverable | Unblocks |
|---|---|---|
| 1 | Profile core + DOCX/PDF renderers | submission at all |
| 2 | ATS question introspection + console + write-back | the one-hour session; first labels |
| 3 | Gmail watcher + state machine | outcomes without manual entry |
| 4 | Prep on trigger | interviews |
| 5 | Conversion ledger + variant promotion + coach | compounding |

Phases 1 and 2 convert 121 drafted packs into submitted applications;
they are the whole ballgame. Phases 3-5 are only meaningful once
applications exist, which is the same argument as section 4.

## 7. Risks

- **Parser mangling.** An ATS-plain `.docx` is a guess until tested.
  Mitigation: run the generated file through an open-source resume
  parser as a test fixture and assert the sections come back intact.
- **Gmail OAuth friction.** A consent screen and a token refresh path
  the user must set up once; read-only scope limits the blast radius.
- **ATS question coverage.** Greenhouse exposes questions well, Lever
  and Ashby less so. The sheet degrades to the standard field set.
- **Thin learning signal.** Thirty applications a month over a handful
  of variants is slow. Report confidence honestly; resist promoting on
  noise.

## 8. Open questions

1. One page or two? MIT format assumes one; staff-level history often
   wants two.
2. Does the console need auth? A published artifact with a resume and
   contact details on it is effectively public to anyone with the link.
3. Which mailbox — personal Gmail, or a dedicated job-hunt address with
   forwarding?
