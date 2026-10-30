#!/usr/bin/env python3
"""Generate the karani.toml reference from the pydantic schema.

`karani.toml` is where a user says what to hunt and which model runs each
task, so its shape is the most-read thing in the project after the CLI. It
is also the thing most likely to drift: a field added to
`karani/config/schema.py` is live the moment it is merged, and a hand-kept
table does not know.

Every section, field, type and default below is read from
`KaraniConfig.model_fields` on each docs build. The prose that explains a
section is hand-written here, keyed by section name, and the build fails on
a section with no prose -- a config block a reader cannot interpret is worse
undocumented-but-listed than absent, because it looks explained.

Secrets are deliberately absent, and that is checked: a field whose name
looks like a credential fails the build. API keys live in `.env`
(CLAUDE.md 4.8), and `api_key_env` holds the *name* of an environment
variable, never a key.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdx import escape_inline  # noqa: E402
import types
from typing import Any, Literal, Union, get_args, get_origin

OUT = Path(__file__).resolve().parents[1] / "content/reference/config.mdx"

# Why a section exists and what to put in it. Keyed by the field name on
# KaraniConfig. The build fails on a section that is missing from here.
SECTIONS: dict[str, str] = {
    "profile": (
        "What counts as a role you would take: which role families, which "
        "seniority bands, which skills must overlap, which titles to "
        "exclude. This is the first of the two deterministic gates and the "
        "cheapest place to change your mind -- edit it, run `karani "
        "refilter`, and every stored row is re-judged for free.\n\n"
        "`allow_titles` lifts terms *out* of the built-in exclusions. The "
        "defaults encode a generic software-engineering hunt; a candidate "
        "whose own background covers an excluded domain says so here (ADR "
        "0021). Name every term that matches the title you want -- "
        "allowing `computational biologist` does nothing while `biologist` "
        "is still excluded."
    ),
    "shapes": (
        "A qualifying posture. Shapes are **additive**: a role qualifies if "
        "it fits any one of them, so this is how two different kinds of "
        "acceptable job coexist without one weakening the other. The "
        "shipped pair is global-remote-at-SF-bands and "
        "relocation-with-sponsorship (ADR 0002).\n\n"
        "Written as repeated `[[shapes]]` tables in TOML."
    ),
    "positioning": (
        "The paragraph templated into every prompt. This is what "
        "de-personalises karani for a second user: nothing about the "
        "candidate is hard-coded in the prompts, it is rendered from here."
    ),
    "legal": (
        "Declared legal facts, and the most consequential block in the "
        "file. Work authorization, visa sponsorship, relocation and start "
        "date are **derived** from these by `karani/legal.py` on every "
        "surface -- the pack, the answer sheet, the console. A model may "
        "receive the resolved line; it never writes one, because these are "
        "attestations you sign and the same question has opposite correct "
        "answers depending on the role's shape (CLAUDE.md 4.12).\n\n"
        "An unknown geography resolves to *needs review*, never to an "
        "assumed yes."
    ),
    "targets": (
        "Per-company ATS boards to poll, as repeated `[[targets]]` tables. "
        "Leave it empty to keep the built-in curated list. A slug is the "
        "company's own identifier on that ATS: the `acme` in "
        "`boards.greenhouse.io/acme`."
    ),
    "llm": (
        "Which model runs each task, independently routable. This is the "
        "block that decides what karani costs: point `[llm.qualify]` at a "
        "local model and bulk qualification is free, while `[llm.draft]` "
        "keeps a strong hosted model for the thing that actually gets the "
        "interview.\n\n"
        "Every task falls back to `[llm.default]` for any knob it does not "
        "set. `provider` is any name in the registry -- `openrouter`, "
        "`openai`, `anthropic`, `local`, or one your own code registered "
        "(ADR 0017). `api_key_env` is the **name** of an environment "
        "variable, so pointing the `openai` provider at Groq or Together "
        "never puts a key in this file."
    ),
    "memory": (
        "What karani retains between passes. `mode` is the switch: `off`, "
        "`basic` (a deterministic ledger in Postgres, the default) or "
        "`mem0` (a semantic index over that same ledger). The ledger is "
        "the system of record and the index is derived and disposable, so "
        "any mem0 failure degrades to `basic` rather than losing anything "
        "(ADR 0009)."
    ),
    "slack": (
        "The two-way Slack surface. Tokens live in `.env`; this says which "
        "conversation and, optionally, who may drive it. An empty "
        "`allowed_users` allows everyone, which is correct for a "
        "single-user DM and wrong for a shared channel."
    ),
    "notion": (
        "The Notion mirror. One-way: Postgres stays the source of truth "
        "(ADR 0011). `karani notion init` prints the database id to put "
        "here."
    ),
    "artifacts": (
        "Per-job object storage for the rendered documents, and the "
        "presigned links that let you open one on a phone (ADR 0014)."
    ),
    "autopilot": (
        "The continuous hunt's spending limits. Triple-bounded on purpose: "
        "a fit floor, a per-run cap, and one shared daily budget across "
        "all 24 hourly runs. Setting the per-run cap to 0 disables "
        "drafting without disabling the pass.\n\n"
        "A fit score is only comparable within the model and prompt "
        "version that produced it, so **a floor tuned against one scorer "
        "will match almost nothing under another** -- measured over 598 "
        "rows, one model's mean was 38 and another's 47 (ADR 0022). Change "
        "the qualify model and re-check this number."
    ),
    "hunt": (
        "How much one pass may take on. `qualify_limit` is the knob that "
        "has to fit the hour: on a local model a row takes 75-170 seconds, "
        "so 15 rows is about half an hour and a pass that cannot finish "
        "silences every pass after it (ADR 0025)."
    ),
    "watcher": (
        "How much mail the response watcher reads. Credentials are "
        "env-only (`GMAIL_*`); this block only says how far back to look. "
        "The OAuth scope is `gmail.readonly` and nothing else, so the "
        "watcher cannot send, delete or mark anything read."
    ),
    "budget": (
        "karani's slice of the LLM account -- not the whole balance. Past "
        "the cap, billed providers are swapped for the local model and the "
        "pass announces the change once. Billed work degrades; it never "
        "halts and never overspends (ADR 0019)."
    ),
    "coach": (
        "Interview prep on a state change. It is billed and unattended, so "
        "it has a switch within reach: this field, and "
        "`KARANI_AUTO_PREP=off` at the shell, which overrides it "
        "(ADR 0024)."
    ),
    "profile_sources": (
        "Where the profile core reads your own records from. The LinkedIn "
        "path is the official data export, never a scraper."
    ),
    "version": (
        "The schema version this file is written against. karani refuses a "
        "file newer than it understands rather than guessing at a field it "
        "has never seen."
    ),
}

# A TOML example per section, so the page shows use and not just shape.
# Companies and people in these are fabricated (see scripts/fixtures.py).
EXAMPLES: dict[str, str] = {
    "profile": """[profile]
roles = ["software_engineering", "ml_ai", "research"]
seniority = ["senior", "staff", "principal"]
must_have_any = ["python", "go", "rust", "sql"]
# Lift a built-in exclusion for a domain your own background covers.
allow_titles = ["computational biologist", "biologist", "bioinformatics"]
min_skill_overlap = 2""",
    "shapes": """# Shape one: they pay San Francisco bands wherever you live.
[[shapes]]
name = "global-remote-sf-bands"
remote = "required"
comp_floor_usd = 160000

# Shape two: they sponsor a visa, and local top-of-market is fine there.
[[shapes]]
name = "relocation-eu-japan"
remote = "any"
destinations = ["DE", "NL", "IE", "JP"]
requires = ["visa_sponsorship"]""",
    "legal": """[legal]
citizenships = ["KE"]
based_in_country = "KE"
work_authorized_in = ["KE"]
# How a remote employer can engage you without a visa.
remote_engagement = ["employer_of_record", "contractor"]
relocation_open_to = ["DE", "NL", "IE", "JP"]
notice_period_days = 30""",
    "targets": """[[targets]]
source = "greenhouse"
slug = "northwind-robotics"
display_name = "Northwind Robotics"

[[targets]]
source = "lever"
slug = "helixdata"
display_name = "Helix Data\"""",
    "llm": """# The recommended split: free local qualification, hosted drafting.
[llm.qualify]
provider = "local"
model = "qwen3:4b"

[llm.draft]
provider = "openrouter"
model = "moonshotai/kimi-k2-thinking"

# Any OpenAI-compatible cloud, with the key staying in the environment.
[llm.humanize]
provider = "openai"
model = "llama-3.3-70b-versatile"
base_url = "https://api.groq.com/openai/v1"
api_key_env = "GROQ_API_KEY\"""",
    "budget": """[budget]
monthly_usd = 15.0
warn_at = 0.8""",
    "autopilot": """[autopilot]
min_fit = 85
max_drafts = 3
max_drafts_per_day = 5
verify = false""",
    "watcher": """[watcher]
lookback_days = 14
max_messages = 50
query = "-category:promotions\"""",
}

# A field name is credential-shaped when one of its underscore-separated
# parts is one of these. Matching on parts rather than substrings is what
# keeps `max_tokens` -- a count, not a credential -- out of it, while
# `slack_token` and `client_secret` are caught.
SECRET_PARTS = frozenset(
    {"token", "secret", "password", "passwd", "key", "credential", "credentials", "dsn"}
)
# ...except this field, which holds the NAME of an environment variable.
SECRET_SHAPED_ALLOWED = {"api_key_env"}


def type_name(annotation: Any) -> str:
    """A pydantic annotation, as a reader of TOML would say it.

    `str | None` is written as `string`, because in TOML the alternative to
    a string is leaving the key out, and "string or null" invites someone to
    write `null`. A `Literal` becomes its accepted values, which is the one
    thing a reader cannot guess.
    """
    origin = get_origin(annotation)
    if origin is Literal:
        return ", ".join(f"`{v}`" for v in get_args(annotation))
    if origin is Union or origin is types.UnionType:
        inner = [type_name(a) for a in get_args(annotation) if a is not type(None)]
        return " or ".join(dict.fromkeys(inner))
    if origin is tuple:
        args = [a for a in get_args(annotation) if a is not Ellipsis]
        return f"{type_name(args[0])}[]" if args else "array"
    if origin in (list, set, frozenset):
        args = get_args(annotation)
        return f"{type_name(args[0])}[]" if args else "array"
    simple = {str: "`string`", int: "`integer`", float: "`number`", bool: "`boolean`"}
    if annotation in simple:
        return simple[annotation]
    if hasattr(annotation, "model_fields"):
        return f"table (`{annotation.__name__}`)"
    return f"`{getattr(annotation, '__name__', annotation)}`"


def cell(text: str) -> str:
    """Table-cell safe. Types and defaults legitimately contain a pipe (a
    union) and an angle bracket (a URL template), and both would otherwise
    end the cell or start a JSX tag."""
    return escape_inline(text, table_cell=True)


def default_text(field: Any) -> str:
    """The default, written the way it would appear in TOML."""
    from pydantic_core import PydanticUndefined

    value = field.default
    if value is PydanticUndefined:
        factory = field.default_factory
        if factory is None:
            return "*required*"
        value = factory()
    if value is None:
        return "*unset*"
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    if isinstance(value, (tuple, list, set, frozenset)):
        if not value:
            return "`[]`"
        # Listed in full, however long. A reader looking up a default wants
        # the value; "7 entries" sends them to the source to find out what
        # the generator already knew.
        return f"`[{', '.join(chr(34) + str(v) + chr(34) for v in value)}]`"
    if hasattr(value, "model_fields"):
        return "*all defaults*"
    if isinstance(value, str):
        return f'`"{value}"`' if value else "*empty*"
    return f"`{value}`"


def field_rows(model: Any, problems: list[str], where: str) -> list[str]:
    """A model's fields as table rows."""
    rows = ["| Key | Type | Default |", "|---|---|---|"]
    for name, field in model.model_fields.items():
        parts = set(name.lower().split("_"))
        if name not in SECRET_SHAPED_ALLOWED and parts & SECRET_PARTS:
            problems.append(
                f"`{where}.{name}` looks like a credential. Secrets belong "
                f"in .env, never in karani.toml (CLAUDE.md 4.8). If it "
                f"holds the NAME of an environment variable, add it to "
                f"SECRET_SHAPED_ALLOWED in web/scripts/generate_config.py."
            )
        rows.append(
            f"| `{name}` | {cell(type_name(field.annotation))} "
            f"| {cell(default_text(field))} |"
        )
    return rows


def nested_models(model: Any) -> list[tuple[str, Any]]:
    """Sub-tables of a model, in declaration order.

    `[llm]` is the only section with these: every task is an `LlmTask`, and
    they are all the same shape, so the page documents the shape once and
    lists the task names rather than repeating one table ten times.
    """
    found: list[tuple[str, Any]] = []
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if hasattr(annotation, "model_fields"):
            found.append((name, annotation))
    return found


def unwrap(annotation: Any) -> Any:
    """The model inside a `tuple[Model, ...]`, or the annotation itself."""
    if hasattr(annotation, "model_fields"):
        return annotation
    for arg in get_args(annotation):
        if hasattr(arg, "model_fields"):
            return arg
    return annotation


def render_section(name: str, field: Any, problems: list[str]) -> list[str]:
    """One `[section]` of the file."""
    model = unwrap(field.annotation)
    repeated = model is not field.annotation
    heading = f"[[{name}]]" if repeated else f"[{name}]"

    out = [f"## {heading}", ""]
    if name not in SECTIONS:
        problems.append(
            f"`{heading}` has no prose in SECTIONS "
            f"(web/scripts/generate_config.py). A config block a reader "
            f"cannot interpret is worse listed than absent."
        )
        return []
    out += [SECTIONS[name], ""]

    if not hasattr(model, "model_fields"):
        # A scalar top-level key (`version`), not a table.
        out += [
            "| Key | Type | Default |",
            "|---|---|---|",
            f"| `{name}` | {cell(type_name(field.annotation))} "
            f"| {cell(default_text(field))} |",
            "",
        ]
        return out

    nested = nested_models(model)
    if nested:
        shapes = {id(m) for _, m in nested}
        if len(shapes) == 1:
            # Every sub-table is the same shape: document it once.
            shape = nested[0][1]
            tasks = ", ".join(f"`[{name}.{task}]`" for task, _ in nested)
            out += [
                f"Sub-tables, each the same shape: {tasks}.",
                "",
                "Any knob a task leaves unset falls back to the one in",
                "`[llm.default]`, so a single `provider` there covers every",
                "task you have not routed by hand.",
                "",
                *field_rows(shape, problems, f"{name}.<task>"),
                "",
            ]
            return out

    out += [*field_rows(model, problems, name), ""]

    if name in EXAMPLES:
        out += ["```toml", EXAMPLES[name], "```", ""]
    return out


def main() -> int:
    try:
        from karani.config.schema import KaraniConfig
    except ImportError as exc:
        print(
            f"error: cannot import karani.config.schema ({exc}).\n"
            "The config reference is generated from the pydantic models, so "
            "the docs build needs karani importable. `uv sync` in the repo "
            "root.",
            file=sys.stderr,
        )
        return 1

    problems: list[str] = []
    body: list[str] = []
    for name, field in KaraniConfig.model_fields.items():
        body += render_section(name, field, problems)

    stale = [s for s in SECTIONS if s not in KaraniConfig.model_fields]
    for s in stale:
        problems.append(
            f"SECTIONS documents `[{s}]`, which KaraniConfig no longer has. "
            f"Remove it from web/scripts/generate_config.py."
        )

    if problems:
        print(f"error: {len(problems)} problem(s) in the config schema:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    sections = len(KaraniConfig.model_fields)
    page = [
        "---",
        'title: "karani.toml"',
        'description: "Every section and key of the config file, generated from the pydantic schema."',
        "---",
        "",
        "# karani.toml",
        "",
        "<Lede>",
        f"{sections} sections, all optional. The defaults are the behaviour",
        "karani had before the file existed, so an empty `karani.toml` and no",
        "`karani.toml` at all are the same thing -- and `karani init` writes",
        "a populated one for you.",
        "</Lede>",
        "",
        "This file owns **what to hunt** and **which model runs each task**.",
        "Changing either is a config edit, not a code edit: that is the point",
        "of the file (ADR 0015). After a change, `karani refilter` re-judges",
        "every stored row for free, and newly-passing rows queue for the next",
        "qualify pass.",
        "",
        "Resolution order, loosest first: the defaults below, then this file,",
        "then an environment variable, then a command-line flag. So",
        "`QUAL_MODEL=... karani qualify` is a one-off experiment and",
        "`karani qualify --model ...` is a one-shot, while the file is what",
        "you actually run on.",
        "",
        "karani looks for the file in the working directory, then",
        "`~/.karani/`. `karani config check` prints what it resolved to.",
        "",
        '<Note kind="warning" title="No secrets in this file">',
        "",
        "API keys, tokens and database URLs live in `.env` and are read from",
        "the environment (CLAUDE.md 4.8). `api_key_env` is the only key here",
        "that mentions one, and it holds the **name** of an environment",
        "variable -- `\"GROQ_API_KEY\"`, not the key. A field whose name looks",
        "like a credential fails the docs build, so this stays true.",
        "",
        "</Note>",
        "",
        '<Note kind="tip" title="This page is generated from the schema">',
        "",
        "Every section, key, type and default is read from",
        "`karani/config/schema.py` on each docs build, so the table cannot",
        "drift from what the loader accepts. The prose and the TOML examples",
        "are hand-written in `web/scripts/generate_config.py`, and a section",
        "with no prose fails the build rather than appearing as a bare table.",
        "",
        "</Note>",
        "",
        *body,
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(page).rstrip() + "\n", encoding="utf-8")
    print(f"generated the config reference: {sections} sections")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
