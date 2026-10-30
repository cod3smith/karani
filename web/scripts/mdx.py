"""Make arbitrary text safe to serve as MDX.

Every generator here embeds text it did not write -- an argparse help
string, an MCP tool's docstring, a pydantic default, a whole ADR -- into
MDX. MDX is not markdown: it reads `<` followed by almost anything as the
start of a JSX tag and `{` as the start of an expression, and it fails the
build rather than rendering the character.

The corpus is full of both. `--notes <title>`, `company <company>`,
`{"set_status": "offer"}` in the watcher's prompt-injection note, `<100ms`
in a latency claim, `<https://...>` autolinks in the ADRs. None of them is
JSX and all of them break the build.

Two functions, differing only in how much structure they preserve:

    escape_inline   for a sentence or a table cell
    escape_block    for a whole document, fenced code and all

Both leave code exactly as written. That is the half that matters: most of
the angle brackets in this project are inside backticks, where they are
correct and must not turn into entities a reader can see.
"""

from __future__ import annotations

import re

# `<` that MDX would read as a tag, or a brace it would read as an
# expression. Escaped as entities, which markdown renders as the character.
_SIGILS = (("{", "&#123;"), ("}", "&#125;"))
# A pipe inside a table cell ends the cell, in every markdown dialect.
_PIPE = ("|", "\\|")


def _escape_outside_code(text: str) -> str:
    """Escape MDX sigils in `text`, leaving inline code spans alone."""
    pieces = re.split(r"(`+[^`]*`+)", text)
    for n, piece in enumerate(pieces):
        if piece.startswith("`"):
            continue
        for old, new in _SIGILS:
            piece = piece.replace(old, new)
        # Every bare `<` goes, not only the ones followed by a letter:
        # `<100ms` and `<=` are as fatal to MDX as `<title>` is, and none of
        # the text these generators embed ever contains real JSX.
        piece = piece.replace("<", "&lt;")
        pieces[n] = piece
    return "".join(pieces)


def escape_inline(text: str, *, table_cell: bool = False) -> str:
    """One sentence, or one table cell, made MDX-safe.

    `table_cell` additionally escapes pipes, which would otherwise end the
    cell -- a type like `int | None` or a Gmail query like
    `-category:promotions` reaches a cell as itself.
    """
    out = _escape_outside_code(text)
    if table_cell:
        old, new = _PIPE
        out = out.replace(old, new)
    return out


def escape_block(text: str) -> str:
    """A whole markdown document made MDX-safe.

    Fenced code is passed through untouched -- that is where most of the
    angle brackets in this project live, and where they are correct. Outside
    a fence, a bare autolink becomes a real link (MDX does not support the
    `<https://...>` form) and the sigils are escaped.
    """
    out: list[str] = []
    in_fence = False
    fence_mark = ""
    for line in text.split("\n"):
        opener = re.match(r"^\s*(`{3,}|~{3,})", line)
        if opener and not in_fence:
            in_fence, fence_mark = True, opener.group(1)[0]
            out.append(line)
            continue
        if in_fence:
            if re.match(rf"^\s*\{fence_mark}{{3,}}\s*$", line):
                in_fence = False
            out.append(line)
            continue

        # The autolink has to be rewritten before `<` is escaped, or the
        # entity hides the pattern.
        line = re.sub(r"<(https?://[^>\s]+)>", r"[\1](\1)", line)
        out.append(_escape_outside_code(line))
    return "\n".join(out)
