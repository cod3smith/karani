"""`karani init` — interactive setup that writes karani.toml.

Stdlib-only (input()); `--yes` accepts every default for scripted setup.
Secrets are never asked for here — the wizard's last words point at .env.
"""
from __future__ import annotations

from pathlib import Path

TEMPLATE = '''\
# karani.toml — what to hunt, which providers, which endpoints.
# Secrets NEVER live here: API keys and tokens go in .env / environment.
# Docs: CONTRIBUTING.md and docs/adrs/0015. Re-judge stored roles after
# edits with: karani refilter
version = 1

[profile]
roles = {roles}
seniority = {seniority}
must_have_any = {skills}
exclude_titles = {exclude_titles}

[positioning]
based_in = "{based_in}"
candidate = "{candidate}"
narrative = "{narrative}"

{shapes}
[llm.default]
provider = "{provider}"        # openrouter | anthropic | local
model = "{model}"

[llm.humanize]                 # line-editing needs no big model
provider = "{humanize_provider}"
model = "{humanize_model}"

[autopilot]
min_fit = {min_fit}
max_drafts_per_day = 5

# [integrations] — uncomment as you connect them (ids here, tokens in .env)
# [slack]
# channel = "D0XXXXXXX"
# allowed_users = ["U0XXXXXXX"]
# [notion]
# database_id = "..."
'''

SHAPE_REMOTE = '''[[shape]]
name = "global-remote"
remote = "required"
comp_floor_usd = {floor}
'''

SHAPE_RELOC = '''[[shape]]
name = "relocation"
destinations = {dests}
requires = ["visa_sponsorship"]
'''


def _ask(prompt: str, default: str, yes: bool) -> str:
    if yes:
        return default
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def _toml_list(csv: str) -> str:
    items = [x.strip() for x in csv.split(",") if x.strip()]
    return "[" + ", ".join(f'"{x}"' for x in items) + "]"


def run_wizard(path: Path, *, yes: bool = False) -> Path:
    print("karani init — describe your hunt (enter accepts the default)\n")
    roles = _ask("Role categories (comma-sep)",
                 "software_engineering, ml_ai, research", yes)
    seniority = _ask("Seniority bands", "senior, staff, principal, lead", yes)
    skills = _ask("Must-have skills (any one qualifies)",
                  "python, typescript, go, rust", yes)
    exclude = _ask("Title words to hard-exclude", "", yes)
    based_in = _ask("You are based in", "City, Country", yes)
    candidate = _ask("Describe yourself in a phrase",
                     "a senior software engineer", yes)
    floor = _ask("Minimum comp (USD) for remote roles", "160000", yes)
    reloc = _ask("Also accept relocation+visa roles? (y/n)", "y", yes)
    dests = (_ask("Preferred destinations", "EU, Japan", yes)
             if reloc.lower().startswith("y") else "")
    provider = _ask("LLM provider", "openrouter", yes)
    model = _ask("LLM model", "moonshotai/kimi-k2-thinking", yes)
    min_fit = _ask("Autopilot fit floor (0-100)", "85", yes)

    narrative = (
        f"targeting fully-remote roles at globally-distributed companies "
        f"paying at least ${int(floor):,} base"
        + (f", or roles that sponsor a visa and relocation "
           f"({dests} preferred)" if dests else "")
    )
    shapes = SHAPE_REMOTE.format(floor=floor)
    if dests:
        shapes += "\n" + SHAPE_RELOC.format(dests=_toml_list(dests))

    content = TEMPLATE.format(
        roles=_toml_list(roles), seniority=_toml_list(seniority),
        skills=_toml_list(skills), exclude_titles=_toml_list(exclude),
        based_in=based_in, candidate=candidate, narrative=narrative,
        shapes=shapes, provider=provider, model=model,
        humanize_provider="local" if provider != "local" else provider,
        humanize_model="llama3.2:3b",
        min_fit=min_fit,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    print(f"\nwrote {path}")
    print("next steps:")
    print("  1. put provider keys in .env (see .env.example)")
    print("  2. cp data/resume.md.example data/resume.md  — and edit it")
    print("  3. karani config check   — see the resolved configuration")
    print("  4. karani refilter       — re-judge any stored roles")
    print("  5. karani hunt           — schedule the hourly hunt")
    return path
