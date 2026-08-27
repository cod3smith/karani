# 0015 · One namespace, one config file, one CLI — pip-installable

**Status:** accepted

## Context

Going open source exposed three structural debts at once: (1) eleven flat
top-level packages (`ingestion`, `memory`, `artifacts`, …) — generic
names that collide in any environment that installs karani; (2) "what to
hunt" was smeared across four *code* files (profile skills, config
signals, curated targets, and prompts that literally hardcoded "Nairobi"
and one person's thesis) — a cloner had to edit Python in four places;
(3) ~30 scattered `os.getenv` calls, several at import time — the hidden
global state behind the test-isolation incident (21d5e0a). And the
project should be `pip install karani` — the name was free on PyPI.

## Decision

**Namespace.** Everything moves under `karani/` (`karani.ingestion`,
`karani.memory`, …), preserved as git renames. One console script:
`karani` (`karani.cli:main`, also `python -m karani`) with ~30 verbs —
pipeline, review, context, and setup (`init`, `config`, `hunt`, `infra`,
`mcp`, `slack`, `hourly`). The Makefile survives as thin aliases.

**Config.** `karani.toml` + the `karani.config` package (ADR-blueprinted
in chat, refined here):

- **Secrets never live in the file.** The file owns structure (providers,
  models, endpoints, buckets, targeting); env owns keys. `karani config
  check` cross-validates ("provider openrouter needs OPENROUTER_API_KEY")
  and shows the resolved config.
- **Precedence: defaults < karani.toml < env.** Defaults reproduce
  pre-config behavior exactly (golden-tested), so the file is optional
  and every existing `.env` keeps working.
- **Discovery:** `$KARANI_CONFIG` > `./karani.toml` (repo mode) >
  `~/.karani/karani.toml` (installed mode). `karani init` writes it
  (stdlib wizard, `--yes` for defaults); `karani.example.toml` ships in
  the wheel.
- **Schema:** `[profile]` (roles/seniority/skills/exclusions),
  `[[shape]]` (additive hunt postures — global-remote vs relocation),
  `[positioning]` (rendered into every prompt — prompts bumped to
  qual-v4 / draft-v3 / prep-v2 and are no longer personal),
  `[[target]]`, `[llm.<task>]` (per-task provider routing: qualify /
  draft / humanize / tailor / prep / followup / agent — closes audit
  H3), `[memory]`, `[slack]` (incl. `allowed_users` — closes H4),
  `[notion]`, `[artifacts]`, `[autopilot]`, `[hunt]`. A `version` field
  gates forward compatibility.
- **Application:** only entry points load config; a single `_apply()`
  bridges profile/targets/gates onto the deterministic tier, and
  consumers resolve knobs at call time through one `resolve(env, file,
  default)` helper — import-time env reads are gone from the paths that
  had them. mem0's PostHog telemetry is disabled by default (H5).

**Packaging.** hatchling wheel of the single `karani` package, with
compose file, launchd templates, and the example config shipped as
package resources — `karani infra up` and `karani hunt` work from a
bare pip install by materializing resources into `~/.karani`. Full PyPI
metadata; distribution name `karani` (verified free). Publishing is
`uv build && uv publish` with the owner's PyPI token — never automated.

## Consequences

- **Positive:** `uv tool install karani && karani init && karani hunt`
  is the entire onboarding. Any provider, any infra, one file.
- **Positive:** the import-time-env class of bug is structurally gone
  where it bit us, and per-task routing cuts pack latency/cost.
- **Negative:** every import path changed. Mitigated: git renames
  preserve history; 191 tests green through the move; no external
  consumers existed pre-publish.
- **Negative:** two config-shaped surfaces remain temporarily
  (`karani.toml` + legacy env names). Deliberate: env compatibility is
  the migration path, revisit removal at 1.0.
