#!/usr/bin/env python3
"""Generate the CLI reference from karani's argparse parser itself.

karani has about thirty verbs and their flags existed only in `--help`. A
reader who wanted to know what `--agent` costs, or which statuses `status`
accepts, had no page to go to.

Written from the parser rather than by hand, because a hand-kept copy of a
`--help` output is wrong the first time somebody adds a flag. The build
fails if a command has no help text, since an undocumented verb in a
reference table is worse than an absent one -- it looks checked.

**How the parser is obtained.** `karani/cli.py` builds its parser inline in
`main()` and there is no `build_parser()` to import. Rather than reshape
runtime code to suit the docs, this intercepts `parse_args`: `main()` builds
the parser, then calls `parser.parse_args()`, and the patch below captures
`self` at that moment and unwinds. If `main()` is ever restructured so the
parser is not built before `parse_args`, nothing is captured and this exits
non-zero with that message rather than writing a thinner page.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdx import escape_inline  # noqa: E402
from typing import Any

OUT = Path(__file__).resolve().parents[1] / "content/reference/cli.mdx"

# Verbs grouped the way a reader uses them, rather than the order argparse
# happens to hold. A verb missing from every group lands in "Everything else",
# so adding one to the CLI cannot silently drop it off this page.
GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "Setup",
        "Run once, then when the hunt changes.",
        ("init", "config", "infra", "hunt", "unschedule"),
    ),
    (
        "The hunt",
        "Fetching postings and deciding which deserve a model call. "
        "`run` and `qualify` are the two tiers (ADR 0001); `hourly` is "
        "both plus delivery, which is what the scheduler actually runs.",
        ("run", "qualify", "refilter", "discover", "sweep", "hourly", "autopilot"),
    ),
    (
        "Reviewing and applying",
        "The daily loop: see what came in, build the pack, send it "
        "yourself, record that you did.",
        ("actions", "digest", "draft", "console", "submitted", "verdict"),
    ),
    (
        "Tracking an application",
        "The state machine, and what fires off it. Moving an application "
        "to `screen` or `interview` builds an interview pack by itself "
        "(ADR 0024).",
        ("status", "stage", "asked", "outcome", "prep", "followup", "watch"),
    ),
    (
        "Measuring",
        "Conversion, segmented and interval-reported (ADR 0022). A bare "
        "percentage over a handful of applications is a lie told with "
        "arithmetic, so these never print one.",
        ("stats", "funnel", "coach", "variant"),
    ),
    (
        "Memory and intel",
        "What karani retains between passes.",
        ("remember", "recall", "reindex", "intel"),
    ),
    (
        "Other surfaces",
        "The same pipeline, different front doors. All three are thin "
        "adapters over the same storage and runners.",
        ("mcp", "slack", "notify", "notion"),
    ),
]

# What a verb is for, beyond its one-line argparse help: the sentence a
# reader needs before the flags mean anything. Only the verbs that need one.
PREAMBLE: dict[str, str] = {
    "run": (
        "The deterministic tier, and the only one that is free. It fetches "
        "every configured board and feed, classifies each posting's role "
        "and seniority, runs the pre-filter, de-duplicates across sources, "
        "stores what survives and closes what has gone stale. **No model "
        "is called.** About 95% of a pass ends here."
    ),
    "qualify": (
        "The LLM tier. Top-scored pending rows go to a model with your "
        "resume, your positioning and your past verdicts as few-shot "
        "examples, and come back with a fit score, a three-way verdict, "
        "evidence-backed strengths and gaps with mitigations. **Billed** "
        "unless `[llm.qualify]` points at a local model, which the "
        "reference config does. Idempotent per resume hash: change your "
        "resume and the next run re-judges, leave it alone and a second "
        "run costs nothing."
    ),
    "refilter": (
        "Re-runs the pre-filter over every active row. This is what makes "
        "changing the rules a config edit rather than a re-ingest: edit "
        "`karani.toml`, run this, and newly-passing rows queue for the "
        "next qualify pass."
    ),
    "console": (
        "Builds the submission console: one self-contained HTML file with "
        "every pack waiting to be sent. Per job, the apply link, presigned "
        "links to the rendered documents, and a field-by-field answer "
        "sheet with a copy button on each field -- answering that job's "
        "**real** questions, read from the ATS's own public API. Free: no "
        "model is called. It works on a phone, which is where the queue "
        "actually gets cleared."
    ),
    "submitted": (
        "Records what you actually sent: status, timestamp, and a snapshot "
        "of the artifact version that went out. That write-back is what "
        "arms the follow-up logic and gives the conversion funnel its "
        "labels, so skipping it costs you the measurement, not just the "
        "bookkeeping. The console prints the exact line to run."
    ),
    "draft": (
        "The pack factory: a tailored resume, a cover letter in your "
        "voice, and answers to the application's questions. Every claim "
        "about you is a selection over your own profile records with the "
        "citation verified before it ships -- an unverifiable claim is "
        "dropped, not repaired (CLAUDE.md 4.13). **Billed.**"
    ),
    "verdict": (
        "Your reaction to a suggestion. This is the taste-calibration "
        "signal: it becomes a few-shot example in the next qualify prompt "
        "and a distilled fact in the memory ledger, so the pipeline's "
        "judgement converges on yours without any retraining."
    ),
    "watch": (
        "Reads recent replies over a **read-only** Gmail scope, classifies "
        "each one, matches it to an application conservatively, advances "
        "the state machine only on a confident match, and drafts a reply "
        "for review. It cannot send, delete or mark anything read -- the "
        "scope makes all three impossible, not the code."
    ),
    "prep": (
        "An interview prep pack: a refreshed company dossier, likely "
        "questions derived from the qualification's gap analysis, STAR "
        "stories mined from your own records with every citation verified, "
        "comp research derived from the posted band, and questions to ask. "
        "Usually you never type this -- moving an application to `screen` "
        "or `interview` fires it. **Billed.**"
    ),
    "funnel": (
        "Conversion by positioning angle, variant, company, comp band and "
        "posting age, each rate with its interval. Fit bands are pooled "
        "only when one scorer produced every score, because a fit score "
        "means nothing across models: measured over 598 rows, one model's "
        "mean was 38 and another's 47 (ADR 0022)."
    ),
    "hunt": (
        "Schedules the continuous hunt. Installs the launchd agents: the "
        "hourly pass, the twice-daily summaries, and an out-of-process "
        "watchdog that is deliberately nothing but `sh` and `curl` so it "
        "cannot die the same way the pipeline does (ADR 0018)."
    ),
    "hourly": (
        "One full pass: ingest, qualify the arrivals, draft packs for "
        "new top-fit roles, deliver review cards. This is what the "
        "scheduler runs. It runs under a hard deadline, because a pass "
        "that cannot finish silences every pass after it (ADR 0025)."
    ),
    "init": (
        "The interactive setup. Writes `karani.toml`: what to hunt, which "
        "model runs each task, your positioning, your declared legal "
        "facts. Secrets are never written here -- they live in `.env`."
    ),
}

# A worked invocation per verb, so the page shows use and not just syntax.
# Job ids are arbitrary four-digit numbers and the companies are fabricated
# (scripts/fixtures.py); nothing here is a real posting.
EXAMPLES: dict[str, list[tuple[str, str]]] = {
    "run": [
        ("karani run", "one deterministic pass over every source"),
    ],
    "qualify": [
        ("karani qualify --limit 50", "judge the top 50 pending rows"),
        ("karani qualify --provider local --model qwen3:4b", "on a local model, at zero token cost"),
        ("karani qualify --agent --limit 5", "let the qualifier gather evidence first; 5-10x the cost per row"),
    ],
    "digest": [
        ("karani digest --format html --output data/digest.html", "render the shortlist to a file"),
        ("karani digest --format text --limit 5", "the top five, for a terminal"),
    ],
    "draft": [
        ("karani draft 1482", "build the pack for one job"),
        ("karani draft 1482 --provider openrouter", "use a stronger model for drafting than for bulk qualification"),
    ],
    "console": [
        ("karani console", "build the console from every pack in drafting or ready"),
        ("karani console --refresh", "re-read each job's application questions from its ATS"),
    ],
    "submitted": [
        ("karani submitted 1482 1511 --cold", "record two applications sent through the portal"),
        ("karani submitted 1523 --warm", "record one sent through a referral; feeds the warm-vs-cold split"),
    ],
    "status": [
        ("karani status 1482 applied --cold", "move it to applied, and say how"),
        ("karani status 1511 screen", "a reply came in; this also fires the interview prep pack"),
    ],
    "verdict": [
        ("karani verdict 1482 apply", "agree with the suggestion"),
        ("karani verdict 1523 skip", "disagree; the next qualify pass sees this"),
    ],
    "remember": [
        (
            'karani remember "Their screen asked about incident ownership" '
            "--kind question --company 'Northwind Robotics'",
            "bank what they actually asked",
        ),
        (
            'karani remember "Not considering crypto companies" --kind preference',
            "a global preference; every later qualification recalls it",
        ),
    ],
    "watch": [
        ("karani watch --authorize", "once: consent to the read-only scope, print the refresh token"),
        ("karani watch --no-slack", "print the pass instead of pushing it"),
        ("karani watch --days 30", "a wider window"),
    ],
    "funnel": [
        ("karani funnel", "conversion rates, each with its interval"),
        ("karani coach --window-days 7", "the derived weekly review over the same ledger"),
    ],
    "infra": [
        ("karani infra up", "Postgres with pgvector on localhost:5433"),
        ("karani infra up --profile local-llm", "and Ollama on localhost:11434"),
    ],
}


def capture_parser() -> argparse.ArgumentParser | None:
    """karani's top-level parser, taken from `main()` as it is built.

    See the module docstring for why this is an interception rather than an
    import. The sentinel is a private exception class so it cannot be
    confused with a real SystemExit from `load_config`.
    """

    class Captured(Exception):
        pass

    held: dict[str, argparse.ArgumentParser] = {}
    original = argparse.ArgumentParser.parse_args

    def spy(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> Any:
        # Only the top-level parser reaches parse_args; subparsers are
        # reached through it and are available on its _actions.
        held["parser"] = self
        raise Captured

    argparse.ArgumentParser.parse_args = spy  # type: ignore[method-assign]
    try:
        from karani.cli import main

        main()
    except Captured:
        pass
    finally:
        argparse.ArgumentParser.parse_args = original  # type: ignore[method-assign]

    return held.get("parser")


def options_of(parser: argparse.ArgumentParser) -> tuple[list[Any], list[Any]]:
    """A parser's positional and optional actions, minus `--help`."""
    positionals: list[Any] = []
    optionals: list[Any] = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if isinstance(action, argparse._SubParsersAction):
            continue
        (positionals if not action.option_strings else optionals).append(action)
    return positionals, optionals


def flags(action: Any) -> str:
    """`-l, --limit N` -- how the argument is written at a prompt.

    A positional is named by its metavar where it has one, so the table and
    the usage line above it call the same argument the same thing.
    """
    if not action.option_strings:
        return f"`{action.metavar or action.dest}`"
    metavar = action.metavar or (action.dest.upper() if action.nargs != 0 else "")
    joined = ", ".join(action.option_strings)
    return f"`{joined}{' ' + metavar if metavar else ''}`"


def choices_of(action: Any) -> str:
    """The accepted values, where argparse constrains them.

    These are the ones a reader most often wants and most often cannot
    guess -- the application statuses, the verdicts, the digest formats.
    """
    if not getattr(action, "choices", None):
        return ""
    values = ", ".join(f"`{c}`" for c in action.choices)
    return f" One of {values}."


def default_of(action: Any) -> str:
    """The trailing default note, unless the help text already carries one."""
    if action.default in (None, False) or isinstance(action, argparse._StoreTrueAction):
        return ""
    if "default" in (action.help or "").lower():
        return ""
    return f" Default: `{action.default}`."


def describe(action: Any) -> str:
    """An argument's help, as a sentence, without a repeated default.

    An option with no help text is not an error here, unlike a command with
    no description: argparse's own `--help` prints the flag with a blank
    column too, and karani's CLI leans on self-describing names
    (`--limit`, `--days`). What the table must not do is print an empty
    cell, so the accepted values and the default stand in.
    """
    help_text = " ".join((action.help or "").split())
    # argparse help conventionally carries "(default: x)"; the table has
    # somewhere for that, so it is lifted out rather than printed twice.
    head, _, tail = help_text.partition("(default:")
    sentence = head.strip().rstrip(".")
    body = f"{sentence[:1].upper()}{sentence[1:]}." if sentence else ""
    if tail:
        return f"{body}{choices_of(action)} Default: `{tail.rstrip(').').strip()}`.".strip()
    return f"{body}{choices_of(action)}{default_of(action)}".strip() or "--"


def subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """The verb -> subparser map of a parser that has subcommands."""
    action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    return dict(action.choices) if action is not None else {}


def summaries(parser: argparse.ArgumentParser) -> dict[str, str]:
    """The verb -> one-line summary map.

    `add_parser(name, help=...)` -- which is how karani's CLI is written --
    records the text on the subparsers action's own choice entries, not as
    the subparser's `description`. Reading `sub.description` finds nothing
    and would report every verb as undocumented, so both are consulted and
    the choice entry wins.
    """
    action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    if action is None:
        return {}
    return {
        choice.dest: " ".join((choice.help or "").split())
        for choice in action._choices_actions
    }


def render_verb(
    name: str,
    sub: argparse.ArgumentParser,
    level: int,
    problems: list[str],
    summary: str = "",
) -> list[str]:
    """One verb's section, at heading `level` (3 for a verb, 4 for a nested one)."""
    out: list[str] = []
    label = f"karani {name}"
    hashes = "#" * level

    out += [f"{hashes} {label}", ""]
    if name in PREAMBLE:
        out += [PREAMBLE[name], ""]

    usage = " ".join(sub.format_usage().replace("usage: ", "").split())
    out += ["```bash", usage.replace("[-h] ", ""), "```", ""]

    # Where a preamble exists it says everything the one-line help says and
    # more, so printing both would just repeat the shorter one.
    summary = summary or " ".join((sub.description or "").split())
    if not summary:
        problems.append(f"`{label}` has no `help=` text on its subparser.")
    elif name not in PREAMBLE:
        out += [escape_inline(f"{summary[:1].upper()}{summary[1:].rstrip('.')}."), ""]

    positionals, optionals = options_of(sub)
    for heading, actions in (("Arguments", positionals), ("Options", optionals)):
        if not actions:
            continue
        out += [
            f"{'#' * (level + 1)} {heading}",
            "",
            f"| {heading[:-1]} | Description |",
            "|---|---|",
        ]
        for action in actions:
            out.append(
                f"| {escape_inline(flags(action), table_cell=True)} "
                f"| {escape_inline(describe(action), table_cell=True)} |"
            )
        out.append("")

    if name in EXAMPLES:
        out += [f"{'#' * (level + 1)} Examples", "", "```bash"]
        for command, why in EXAMPLES[name]:
            out += [f"# {why}", command, ""]
        out = [*out[:-1], "```", ""]

    # A verb may have verbs of its own -- `karani config` and `karani notion`
    # do. Flat rather than recursive beyond this: two levels is what the CLI
    # has, and a generator handling depth it never meets would be guessing.
    nested_help = summaries(sub)
    for nested_name, nested in subcommands(sub).items():
        out += render_verb(
            f"{name} {nested_name}",
            nested,
            level + 1,
            problems,
            nested_help.get(nested_name, ""),
        )

    return out


def main() -> int:
    """Write the CLI reference, or fail saying what is undocumented."""
    try:
        parser = capture_parser()
    except ImportError as exc:
        print(
            f"error: cannot import karani.cli ({exc}).\n"
            "The CLI reference is generated from the parser, so the docs "
            "build needs karani importable. `uv sync` in the repo root, or "
            "`pip install -e .`.",
            file=sys.stderr,
        )
        return 1

    if parser is None:
        print(
            "error: karani.cli.main() did not reach parse_args, so no parser "
            "was captured.\n"
            "This generator intercepts parse_args to get the parser without "
            "reshaping runtime code (see the module docstring). If main() "
            "has been restructured, give it a build_parser() and import that "
            "instead.",
            file=sys.stderr,
        )
        return 1

    verbs = subcommands(parser)
    verb_help = summaries(parser)
    if not verbs:
        print("error: the captured parser has no subcommands.", file=sys.stderr)
        return 1

    problems: list[str] = []
    body: list[str] = []
    placed: set[str] = set()

    for heading, blurb, names in GROUPS:
        present = [n for n in names if n in verbs]
        missing = [n for n in names if n not in verbs]
        for name in missing:
            problems.append(
                f"GROUPS lists `karani {name}`, which the parser does not "
                f"have. Remove it from scripts/generate_cli.py."
            )
        if not present:
            continue
        body += [f"## {heading}", "", blurb, ""]
        for name in present:
            placed.add(name)
            body += render_verb(name, verbs[name], 3, problems, verb_help.get(name, ""))

    leftover = [n for n in verbs if n not in placed]
    if leftover:
        body += [
            "## Everything else",
            "",
            "Verbs this page's own grouping has not been taught yet. They "
            "are listed because the generator refuses to drop a verb it "
            "does not recognise -- the groups live in "
            "`web/scripts/generate_cli.py`.",
            "",
        ]
        for name in leftover:
            body += render_verb(name, verbs[name], 3, problems, verb_help.get(name, ""))

    if problems:
        print(f"error: {len(problems)} problem(s) in the CLI:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    page = [
        "---",
        'title: "Command line"',
        'description: "Every karani verb and flag, generated from the argparse parser itself."',
        "---",
        "",
        "# Command line",
        "",
        "<Lede>",
        f"{len(verbs)} verbs, one entry point. Three of them are the whole",
        "daily loop: `karani run` fetches and filters for free, `karani",
        "qualify` spends a model call on what survived, and `karani console`",
        "lays out what is ready to send.",
        "</Lede>",
        "",
        "Every verb returns 0 when all is well and non-zero when it could",
        "not do what you asked, so each one composes into a shell chain.",
        "Long operations print a machine-readable summary line",
        "(`fetched=N inserted=N passed=N`) because something is usually",
        "reading the log.",
        "",
        "Billed verbs say so in their description. Every one of them obeys",
        "the monthly budget in `[budget]`: past the cap, a billed provider",
        "is swapped for the local model and the pass says once that it",
        "happened, rather than failing or overspending (ADR 0019).",
        "",
        '<Note kind="tip" title="This page is generated from the parser">',
        "",
        "So it cannot drift from `karani --help`. If a flag is missing here,",
        "it does not exist. The groupings and the worked examples are",
        "hand-written in `web/scripts/generate_cli.py`; the flags, their",
        "accepted values and their defaults are read from the parser on",
        "every docs build, and a verb the grouping has not been taught is",
        "listed rather than dropped.",
        "",
        "</Note>",
        "",
        *body,
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(page).rstrip() + "\n", encoding="utf-8")
    nested = sum(len(subcommands(p)) for p in verbs.values())
    print(f"generated the CLI reference: {len(verbs)} verbs, {nested} subcommands")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
