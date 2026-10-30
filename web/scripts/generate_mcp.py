#!/usr/bin/env python3
"""Generate the MCP tool reference from the running server.

The tool set is asserted by `tests/test_mcp_server.py`, so it is already a
contract; this makes it a readable one. Every name, description and
parameter below is read from `app.list_tools()` -- the same listing an MCP
client receives on connect -- so the page cannot disagree with what a client
is actually offered.

The build fails if a tool has no description. A tool's description is how a
model decides whether to call it, so an empty one is a bug in the server
rather than a gap in the docs, and an empty row in a reference table looks
checked when it is not.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdx import escape_inline  # noqa: E402
from typing import Any

OUT = Path(__file__).resolve().parents[1] / "content/reference/mcp.mdx"

# Tools grouped the way a conversation uses them. A tool in no group lands
# in "Everything else", so adding one to the server cannot drop it off this
# page silently.
GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "Finding work",
        "The two tiers. `ingest` is free and `qualify` is not, which is the "
        "whole shape of the pipeline (ADR 0001).",
        ("ingest", "sweep", "discover", "qualify"),
    ),
    (
        "Reviewing",
        "What came in, and what to do about it today. `next_actions` is the "
        "one to reach for first in a conversation -- it answers \"what "
        "should I do on the job hunt today?\" without the client having to "
        "assemble it from three other calls.",
        ("next_actions", "shortlist", "digest", "get_job", "pipeline_stats"),
    ),
    (
        "Building and sending",
        "The pack, then the console that lays it out, then the write-back "
        "that records what you actually sent. karani never submits (ADR "
        "0012), so there is deliberately no tool here that would.",
        ("draft", "submission_console", "mark_submitted"),
    ),
    (
        "Tracking",
        "The state machine. Moving an application to `screen` or "
        "`interview` through `set_status` fires an interview prep pack by "
        "itself (ADR 0024) -- the tool call that looks like bookkeeping is "
        "the one that spends two model calls.",
        ("set_status", "add_stage", "record_question", "record_outcome", "record_verdict"),
    ),
    (
        "Preparing",
        "For the conversation after the application.",
        ("prep", "draft_followup", "company_intel", "warm_paths"),
    ),
    (
        "Measuring",
        "Conversion, segmented and interval-reported (ADR 0022).",
        ("funnel_stats",),
    ),
    (
        "Memory",
        "What karani retains between conversations. `remember` is the API "
        "for \"I learned something about this company somewhere else\": "
        "every later qualification recalls it.",
        ("remember", "recall"),
    ),
    (
        "Pushing it somewhere",
        "The other surfaces, driven from here.",
        ("notify_slack", "notion_sync", "autopilot"),
    ),
]

# Tools that spend money. Taken from the description text where the server
# says so, and asserted: a billed tool whose description stops saying so is
# a tool an agent can call without knowing the cost.
BILLED = ("qualify", "draft", "prep", "draft_followup", "autopilot")


def schema_rows(schema: dict[str, Any]) -> list[str]:
    """A tool's parameters as table rows, required ones first.

    The description column appears only when the server actually supplies
    one for some parameter of this tool. karani's tool signatures mostly
    describe themselves (`limit`, `days`, `provider`) and carry no pydantic
    `Field(description=...)`, so a column of em-dashes would be noise
    pretending to be documentation.
    """
    properties: dict[str, Any] = schema.get("properties") or {}
    if not properties:
        return []
    required = set(schema.get("required") or [])
    names = sorted(properties, key=lambda n: (n not in required, n))
    described = any((properties[n] or {}).get("description") for n in names)

    if described:
        rows = ["| Parameter | Type | Default | Description |", "|---|---|---|---|"]
    else:
        rows = ["| Parameter | Type | Default |", "|---|---|---|"]
    for name in names:
        spec = properties[name] or {}
        cells = [f"`{name}`", type_of(spec), default_of(spec, name in required)]
        if described:
            cells.append(spec.get("description") or "--")
        rows.append(
            "| " + " | ".join(escape_inline(c, table_cell=True) for c in cells) + " |"
        )
    return rows


def prose(text: str) -> list[str]:
    """A tool docstring as markdown lines.

    Line structure is kept rather than collapsed to one paragraph: several
    of karani's tools document their return shape as a list, and
    `" ".join(text.split())` turns that into an unreadable run of
    "- review: ... - to_draft: ...". Blank lines are preserved so markdown
    sees the paragraph and list breaks the docstring intended, and a list
    that opens straight after a sentence gets the blank line markdown needs.
    """
    lines = [line.rstrip() for line in (text or "").strip().split("\n")]
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        starts_list = stripped.startswith("- ")
        if starts_list and out and out[-1].strip() and not out[-1].lstrip().startswith("- "):
            out.append("")
        # A docstring indents its continuation lines; markdown would read
        # four spaces as a code block, so only a list marker keeps a hanging
        # indent and everything else is flattened to the margin.
        out.append(
            f"- {escape_inline(stripped[2:])}" if starts_list else escape_inline(stripped)
        )
    return out


def type_of(spec: dict[str, Any]) -> str:
    """A JSON-schema type, as a reader would say it.

    Pydantic writes an optional `int` as `anyOf: [integer, null]`, and a
    literal as an `enum`; both are flattened here, because "integer or
    null" tells a reader less than "integer" plus a blank default does.
    """
    if "enum" in spec:
        return ", ".join(f"`{v}`" for v in spec["enum"])
    if "anyOf" in spec:
        inner = [type_of(s) for s in spec["anyOf"] if s.get("type") != "null"]
        return " or ".join(dict.fromkeys(inner)) or "any"
    kind = spec.get("type")
    if kind == "array":
        items = type_of(spec.get("items") or {})
        return f"{items}[]"
    return f"`{kind}`" if kind else "any"


def default_of(spec: dict[str, Any], required: bool) -> str:
    """The default, or the word `required`."""
    if required:
        return "*required*"
    if "default" not in spec:
        return "--"
    value = spec["default"]
    if value is None:
        return "*unset*"
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    return f"`{value}`"


def render_tool(tool: Any, problems: list[str]) -> list[str]:
    """One tool's section."""
    description = (tool.description or "").strip()
    if not description:
        problems.append(
            f"`{tool.name}` has no description. A description is how a model "
            f"decides whether to call a tool, so an empty one is a bug in "
            f"karani/mcp_server/server.py."
        )
        return []

    out = [f"### {tool.name}", ""]
    if tool.name in BILLED:
        if "billed" not in description.lower():
            problems.append(
                f"`{tool.name}` is listed as billed in "
                f"web/scripts/generate_mcp.py but its description does not "
                f"say so. An agent calling it cannot know the cost."
            )
        out += ["**Billed.** Spends a model call.", ""]
    out += [*prose(description), ""]

    schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
    rows = schema_rows(schema)
    if rows:
        out += rows + [""]
    else:
        out += ["Takes no parameters.", ""]
    return out


async def collect() -> list[Any]:
    from karani.mcp_server.server import app

    return list(await app.list_tools())


def main() -> int:
    try:
        tools = asyncio.run(collect())
    except ImportError as exc:
        print(
            f"error: cannot import karani.mcp_server.server ({exc}).\n"
            "The MCP reference is generated from the server, so the docs "
            "build needs karani importable. `uv sync` in the repo root.",
            file=sys.stderr,
        )
        return 1

    if not tools:
        print("error: the server listed no tools.", file=sys.stderr)
        return 1

    by_name = {t.name: t for t in tools}
    problems: list[str] = []
    body: list[str] = []
    placed: set[str] = set()

    for heading, blurb, names in GROUPS:
        present = [n for n in names if n in by_name]
        for name in names:
            if name not in by_name:
                problems.append(
                    f"GROUPS lists `{name}`, which the server does not "
                    f"expose. Remove it from web/scripts/generate_mcp.py."
                )
        if not present:
            continue
        body += [f"## {heading}", "", blurb, ""]
        for name in present:
            placed.add(name)
            body += render_tool(by_name[name], problems)

    leftover = [t.name for t in tools if t.name not in placed]
    if leftover:
        body += [
            "## Everything else",
            "",
            "Tools this page's own grouping has not been taught yet. They "
            "are listed because the generator refuses to drop a tool it "
            "does not recognise -- the groups live in "
            "`web/scripts/generate_mcp.py`.",
            "",
        ]
        for name in leftover:
            body += render_tool(by_name[name], problems)

    for name in BILLED:
        if name not in by_name:
            problems.append(
                f"BILLED lists `{name}`, which the server does not expose."
            )

    if problems:
        print(f"error: {len(problems)} problem(s) in the MCP surface:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    free = len(tools) - len(BILLED)
    page = [
        "---",
        'title: "MCP tools"',
        'description: "Every tool the karani MCP server offers, generated from the server\'s own listing."',
        "---",
        "",
        "# MCP tools",
        "",
        "<Lede>",
        f"{len(tools)} tools over stdio, mapping onto the CLI verbs one for",
        f"one. {free} of them are free; the {len(BILLED)} that spend a model",
        "call say so in the description a client reads, so an agent knows",
        "the cost before it calls.",
        "</Lede>",
        "",
        "The server is a thin adapter and nothing more: every tool calls the",
        "same runners and the same `Storage` methods the CLI calls, and holds",
        "no logic of its own (ADR 0008). That is why the two surfaces cannot",
        "disagree about what `set_status` does.",
        "",
        "Start it with `karani mcp`. [Connecting a client](/guides/mcp/) has",
        "the configuration for Claude Code, Claude Desktop, Cursor and",
        "anything else that speaks stdio.",
        "",
        '<Note kind="tip" title="This page is generated from the server">',
        "",
        "Every name, description, parameter, type and default below is read",
        "from `app.list_tools()` on each docs build -- the same listing your",
        "client receives on connect. The groupings are hand-written in",
        "`web/scripts/generate_mcp.py`; a tool the grouping has not been",
        "taught is listed rather than dropped, and a tool with no",
        "description fails the build.",
        "",
        "</Note>",
        "",
        *body,
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(page).rstrip() + "\n", encoding="utf-8")
    print(f"generated the MCP reference: {len(tools)} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
