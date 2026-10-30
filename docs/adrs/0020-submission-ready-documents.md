# ADR 0020 — Submission-ready documents: ATS-plain .docx, PDF for humans

Date: 2026-10-27
Status: accepted
Extends: 0014 (per-job artifacts in MinIO), 0012 (karani drafts, a human sends)

## Context

The pack was markdown. 121 packs had been generated and 0 applications
had been sent.

Markdown is not an upload format — no ATS accepts `.md`. So every
application began with manual conversion: open the pack, paste it into
a word processor, repair the formatting, export, rename. That is ten
minutes of tedium arriving at exactly the moment the pipeline had
finished all of its own work, and it is a large part of why the funnel
terminated at "drafted". The expensive tiers (ADR 0001) were running
to completion and then handing over something that could not be sent.

The second fact is that resume parsers fail in specific, documented
ways. Tables, text boxes, headers and footers, multi-column layouts
and graphics are what make a parser turn an experience section into
gibberish — and it does so silently. Nothing bounces. The candidate
never learns the resume arrived unreadable, and reads the lack of a
reply as a lack of interest.

The third is that there are two readers with opposite preferences. The
ATS wants something a machine can take apart. The human who opens the
attachment wants something that looks like a resume. A single file
optimised for either one is wrong for the other.

## Decision

**Parse once, render twice.** `parse_markdown` produces a `Document`
(name, contact lines, sections of blocks); `to_docx` and `to_pdf` are
two renderers over that one model, so the two files cannot drift in
content. Layout follows MIT CAPD conventions: centered name and contact
header, standard section headings, strict single column.

**The `.docx` (python-docx) is the artifact to upload.** It uses the
built-in `Heading 1` / `Heading 2` styles and `List Bullet` paragraphs,
because those are what parsers look for; Calibri 10.5pt and MIT margins.
No tables, no text boxes, no headers or footers, no graphics — the list
above, inverted.

**The PDF is for the human**, rendered from the same model through
headless Chrome. Chrome rather than a Python PDF library because it
adds no dependency at all, and because the CSS in `to_html` is then the
single layout spec for the typographic version.

**Filenames carry the candidate, company and role** —
`<Candidate>_<Company>_<Role>_Resume.pdf`, not `resume_final_v2`. The
filename is the first thing a recruiter sees and it is free to get
right.

Both land in the job's MinIO prefix with presigned links, as ADR 0014
already provides for.

**Degradation is layered and deliberate:**

- no Chrome on the machine → no PDF, the `.docx` still ships
- python-docx not installed → no `.docx`, the markdown still ships
- any renderer raising → the pack still ships, with a warning logged

A missing renderer must never cost the application. This is the same
contract the memory layer and the storage fallback already hold to.

## Consequences

- The manual conversion step is gone. What the pipeline produces is
  what gets uploaded.
- python-docx is an optional extra (`documents`), not a core
  dependency, and the PDF path needs no Python dependency whatsoever.
- The `.docx` is deliberately plain and will look plainer than a
  designed resume. That is the trade being made: a parser reads it
  correctly, and the PDF carries the typography for the human.
- Rendering is best-effort, so **artifact sets are not uniform across
  machines**. A pack rendered where Chrome is absent has a `.docx` and
  no `.pdf`. Anything consuming the artifact map must treat both as
  optional — `tests/test_artifacts_pipeline.py` asserts on presence by
  suffix rather than against a fixed set, for this reason.
- Chrome is invoked as a subprocess with a 90-second timeout. It is the
  one place karani shells out to a browser, and it is one of the five
  subprocess tests that take the default suite from ~6s to ~40s.
- MIT CAPD conventions are a choice, not a universal standard. They
  suit the US and global-remote market this pipeline targets (ADR
  0002). A market whose norms differ — a German *Lebenslauf* expects a
  photograph, which this renderer will never emit — is not served by
  it, and extending there means revisiting this decision rather than
  quietly adding a table.
