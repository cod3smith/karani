#!/usr/bin/env python3
"""Publish the architecture decision records as a browsable section.

The ADRs are the project's reasoning, written down as it happened, and they
are the thing most worth reading after the quickstart -- CLAUDE.md's rule is
"don't argue architecture with the code; argue it with an ADR". They lived
as 25 markdown files with a hand-kept index in `docs/adrs/README.md` that
had fallen eight entries behind.

So the index is generated and the files are converted rather than copied.
`docs/adrs/` stays the source of truth: nothing here edits it, and a new ADR
appears on the site on the next docs build with no second place to update.

Two header styles exist in the corpus, because the convention changed:

    # 0001 · Tiered filter ...                 (0001-0016, 0023)
    # ADR 0017 — Pluggable LLM providers       (0017 onward)
    Date: 2026-09-01
    Status: accepted
    Extends: 0015 (config file), 0007 (provider split)

Both are parsed. The later style's metadata lines become the page's own
header; the earlier style has none, and the page says so rather than
inventing a date.

The build fails on an ADR whose number or title cannot be read, because a
decision record nobody can cite is not serving its purpose.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdx import escape_block, escape_inline  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs/adrs"
OUT = ROOT / "web/content/decisions"

# `# 0001 · Title` or `# ADR 0017 — Title`, with either dash or the middot.
HEADER = re.compile(r"^#\s+(?:ADR\s+)?(\d{4})\s*(?:[-·—–:]|--)\s*(.+?)\s*$")
# A preamble metadata line, in either style the corpus uses:
#   Date: 2026-09-01            (0017 onward)
#   **Status:** accepted        (0001-0016, 0023)
#   Supersedes the exclusion in ADR 0003 (pre-filter signals) and
# The last form has no colon and wraps onto the next line, so the key may
# be followed by prose on the same line and a continuation on the next.
META = re.compile(
    r"^\**(Date|Status|Extends|Supersedes|Amends|Replaces|Builds on|Implements)"
    r"\**\s*:?\**\s*(.*)$",
    re.IGNORECASE,
)
# The order they are shown in, after Status and Date.
RELATION_KEYS = ("Extends", "Supersedes", "Amends", "Replaces", "Builds on", "Implements")

# One line per ADR saying what it is about, so the index is scannable and
# the sidebar entries are not 60-character titles. Keyed by number. An ADR
# with no entry here falls back to its own title, which is honest but
# duller -- the build does not fail on a missing one, because a new ADR
# should not block a docs build.
GIST: dict[str, str] = {
    "0001": "Why there are two filter tiers and not one model call per posting.",
    "0002": "The positioning the whole pipeline exists to serve, and why it is location-agnostic.",
    "0003": "Which LLM provider is the default, and which is optional.",
    "0004": "How a posting is recognised as one already seen, across sources.",
    "0005": "Why the application state machine is columns on a row and not its own table.",
    "0006": "Why running with no database is a real code path rather than a test shim.",
    "0007": "Why the tool-using agent is opt-in and never the daily batch.",
    "0008": "Why the MCP server is a thin adapter with no logic of its own.",
    "0009": "Why the memory ledger is the system of record and the vector index is disposable.",
    "0010": "Why Slack is the two-way surface, and what it is allowed to do.",
    "0011": "Why the Notion board is a one-way mirror that karani owns the schema of.",
    "0012": "Why karani drafts everything and submits nothing. The load-bearing decision.",
    "0013": "Where LangGraph is used, and the larger set of places it is not.",
    "0014": "Why each job's documents live in object storage, and why the humanizer is measured.",
    "0015": "One namespace, one config file, one CLI -- and why it is on PyPI.",
    "0016": "The hardening pass: cross-run dedup, advisory locks, a run ledger, a verify gate.",
    "0017": "Providers as a registry, and why bulk qualification runs on a local model.",
    "0018": "Why the thing that watches the pipeline cannot run inside it.",
    "0019": "Why billed work degrades to a local model instead of halting or overspending.",
    "0020": "Why the pack renders to an ATS-plain .docx and a PDF, after 121 packs produced 0 applications.",
    "0021": "Why computational biology is in scope, and how a built-in exclusion becomes liftable.",
    "0022": "A conversion ledger, and a promotion rule that refuses to crown a winner on noise.",
    "0023": "A static submission console, and the write-back that gives the funnel its labels.",
    "0024": "Why interview prep fires on a state change rather than on a verb.",
    "0025": "Why every pass has a deadline, after a hang silenced the pipeline for 40 hours.",
}


@dataclass
class Adr:
    number: str
    title: str
    slug: str
    source: str
    meta: dict[str, str] = field(default_factory=dict)
    body: str = ""

    @property
    def status(self) -> str:
        return self.meta.get("Status", "accepted")

    @property
    def date(self) -> str:
        return self.meta.get("Date", "")

    @property
    def relations(self) -> list[str]:
        """Extends / supersedes / amends, one `**Key** value` phrase each."""
        return [
            f"**{key}** {value}"
            for key in RELATION_KEYS
            if (value := self.meta.get(key))
        ]


def parse(path: Path, problems: list[str]) -> Adr | None:
    """One ADR file.

    The body is everything from the first `## ` section onward, and the
    preamble between the heading and that section is the metadata. Cutting
    at the section heading rather than at a blank line is what handles both
    header styles and the records whose `Supersedes:` wraps onto a second
    line -- an earlier version cut at the first blank line and leaked that
    continuation into the body as a stray sentence.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    header = next((HEADER.match(line) for line in lines if HEADER.match(line)), None)
    if header is None:
        problems.append(
            f"{path.name}: no `# NNNN - Title` or `# ADR NNNN - Title` "
            f"heading. A decision record nobody can cite is not serving "
            f"its purpose."
        )
        return None

    number, title = header.group(1), header.group(2)
    at = lines.index(header.group(0))
    body_at = next(
        (n for n in range(at + 1, len(lines)) if lines[n].startswith("## ")),
        len(lines),
    )

    meta: dict[str, str] = {}
    last_key = ""
    for line in lines[at + 1 : body_at]:
        stripped = line.strip()
        if not stripped:
            last_key = ""
            continue
        found = META.match(stripped)
        if found:
            matched = found.group(1).lower()
            last_key = next(
                (k for k in (*RELATION_KEYS, "Date", "Status") if k.lower() == matched),
                found.group(1).capitalize(),
            )
            # `**Status:** accepted` leaves the closing asterisks on the
            # value, so emphasis markers are stripped from both ends.
            meta[last_key] = found.group(2).strip().strip("*").strip()
        elif last_key:
            # A continuation of the previous key, as ADR 0021's two-line
            # `Supersedes` is.
            meta[last_key] = f"{meta[last_key]} {stripped}".strip()
        else:
            problems.append(
                f"{path.name}: the preamble line {stripped!r} is neither "
                f"metadata nor part of a section. Either give it a "
                f"`Key: value` shape or move it under a `## ` heading."
            )

    body = "\n".join(lines[body_at:]).strip()
    if not body:
        problems.append(
            f"{path.name}: no `## ` section after the heading, so there is "
            f"nothing to publish."
        )
        return None

    slug = path.stem  # `0017-pluggable-llm-providers`
    return Adr(number=number, title=title, slug=slug, source=path.name, meta=meta, body=body)


def page(adr: Adr) -> str:
    """One ADR as an MDX page."""
    gist = GIST.get(adr.number, adr.title)
    facts = [f"**Status** {adr.status}"]
    if adr.date:
        facts.append(f"**Date** {adr.date}")
    facts += adr.relations

    header = [
        "---",
        f'title: "ADR {adr.number} — {escape_inline(adr.title)}"',
        f'sidebarTitle: "{adr.number} {escape_inline(adr.title)}"',
        f'description: "{escape_inline(gist)}"',
        "---",
        "",
        f"# ADR {adr.number} — {adr.title}",
        "",
        '<Note kind="note" title="Decision record">',
        "",
        escape_inline(" &middot; ".join(facts)) + ".",
        "",
        f"The source of truth is [`docs/adrs/{adr.source}`]"
        f"(https://github.com/cod3smith/karani/blob/main/docs/adrs/{adr.source}) "
        "in the repository. This page is generated from it on every docs build.",
        "",
        "</Note>",
        "",
        "",
    ]
    return "\n".join(header) + escape_block(adr.body) + "\n"


def index(adrs: list[Adr]) -> str:
    """The section's landing page: every decision, newest first."""
    rows = ["| ADR | Decision | Status |", "|---|---|---|"]
    for adr in reversed(adrs):
        gist = GIST.get(adr.number, "")
        link = f"[{adr.number}](/decisions/{adr.slug}/)"
        title = escape_inline(adr.title, table_cell=True)
        detail = f"<br />{escape_inline(gist, table_cell=True)}" if gist else ""
        rows.append(f"| {link} | **{title}**{detail} | {escape_inline(adr.status, table_cell=True)} |")

    numbers = [int(a.number) for a in adrs]
    gaps = sorted(set(range(min(numbers), max(numbers) + 1)) - set(numbers))
    gap_note = (
        ""
        if not gaps
        else "\n".join(
            [
                "",
                '<Note kind="note" title="A gap in the numbering">',
                "",
                f"There is no ADR {', '.join(f'{n:04d}' for n in gaps)} in the "
                "repository, so none is published here, although later records "
                "cite the number. Numbering is monotonic and never reused, so a "
                "gap stays a gap rather than being filled in later by something "
                "unrelated.",
                "",
                "</Note>",
                "",
            ]
        )
    )

    return "\n".join(
        [
            "---",
            'title: "Decisions"',
            'description: "Every architecture decision record: the context, the choice, and what it cost."',
            "---",
            "",
            "# Decisions",
            "",
            "<Lede>",
            f"{len(adrs)} decision records, newest first. Each one is the",
            "context, the options, the choice and the consequences -- written",
            "when the decision was made, not reconstructed afterwards.",
            "</Lede>",
            "",
            "An accepted record is never edited to reverse it. A decision that",
            "changes gets a new record that supersedes the old one, and the old",
            "one stays exactly as written, because the reasoning that turned out",
            "to be wrong is the part worth keeping. [ADR",
            "0021](/decisions/0021-computational-bio-is-in-scope/) reversing a",
            "scope exclusion from [ADR",
            "0003](/decisions/0003-openrouter-default-anthropic-optional/) is the",
            "worked example.",
            "",
            "If you are reading one of these first, read [ADR",
            "0012](/decisions/0012-autopilot-draft-human-send/). karani drafts",
            "everything and submits nothing, and almost every other decision here",
            "is downstream of that one.",
            gap_note,
            *rows,
            "",
        ]
    )


def meta_file(adrs: list[Adr]) -> str:
    """The section's `_meta.ts`, in reverse-numeric order.

    Newest first, because a reader arriving at the decisions section wants
    the current thinking, and because the later records cite the earlier
    ones rather than the other way round.
    """
    entries = [
        f"  '{adr.slug}': '{adr.number} {adr.title.replace(chr(39), chr(92) + chr(39))}',"
        for adr in reversed(adrs)
    ]
    return "\n".join(
        [
            "// Generated by web/scripts/generate_adrs.py from docs/adrs/.",
            "// Do not edit: the next docs build overwrites it.",
            "import type { MetaRecord } from 'nextra'",
            "",
            "const meta: MetaRecord = {",
            "  index: 'All decisions',",
            *entries,
            "}",
            "",
            "export default meta",
            "",
        ]
    )


def main() -> int:
    if not SOURCE.is_dir():
        print(f"error: {SOURCE} does not exist.", file=sys.stderr)
        return 1

    problems: list[str] = []
    adrs: list[Adr] = []
    for path in sorted(SOURCE.glob("[0-9][0-9][0-9][0-9]-*.md")):
        adr = parse(path, problems)
        if adr is not None:
            adrs.append(adr)

    if not adrs:
        problems.append(f"no ADR files matched NNNN-*.md in {SOURCE}.")

    stale = [n for n in GIST if n not in {a.number for a in adrs}]
    for n in stale:
        problems.append(
            f"GIST has a line for ADR {n}, which no longer exists in "
            f"docs/adrs/. Remove it from web/scripts/generate_adrs.py."
        )

    if problems:
        print(f"error: {len(problems)} problem(s) in docs/adrs:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    # Clear out pages for ADRs that have been renamed, so a stale slug
    # cannot linger in the build and the sidebar.
    for existing in OUT.glob("*.mdx"):
        existing.unlink()

    for adr in adrs:
        (OUT / f"{adr.slug}.mdx").write_text(page(adr), encoding="utf-8")
    (OUT / "index.mdx").write_text(index(adrs), encoding="utf-8")
    (OUT / "_meta.ts").write_text(meta_file(adrs), encoding="utf-8")

    ungisted = [a.number for a in adrs if a.number not in GIST]
    note = f" ({len(ungisted)} with no one-line gist: {', '.join(ungisted)})" if ungisted else ""
    print(f"generated the decisions section: {len(adrs)} ADRs{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
