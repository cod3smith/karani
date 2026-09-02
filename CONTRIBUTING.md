# Contributing to karani

karani is a personal, semi-autonomous job-application pipeline built in
public. Contributions are welcome — the architecture is deliberately
modular so most changes touch one package.

## Ground rules (read these first)

1. **`CLAUDE.md` section 4 is non-negotiable.** Word-boundary signal
   matching, deterministic pre-filter (no LLM calls in `filters.py`),
   versioned prompts, ledger-first memory, idempotent schema migrations.
   PRs that break a guardrail get closed with a pointer to the rule.
2. **Karani never submits an application.** Anything that auto-submits,
   auto-messages recruiters, or bypasses human review is out of scope —
   see `docs/vision.md` non-goals and ADR 0012.
3. **Argue architecture with ADRs, not code.** Reversing a documented
   decision needs a superseding ADR in `docs/adrs/`.
4. **Every surface stays in sync.** A new verb lands on `Storage`/runners
   first, then CLI + MCP + Slack together (see ADR 0008/0010).

## Setup

```bash
git clone <repo> && cd karani
uv sync --all-extras
cp .env.example .env                     # fill what you use; all optional
cp data/resume.md.example data/resume.md # your resume — never committed
make test                                # 181+ tests, ~2s, no network
```

The whole pipeline runs with zero external services (in-memory storage,
`basic` memory mode). Postgres/Slack/Notion/MinIO/Ollama are additive.

## Testing conventions

- Deterministic only: no network, no clock. Fake LLM clients (see
  `tests/test_qualification.py`), `httpx.MockTransport` for HTTP,
  `Storage("")` for the DB. `conftest.py` strips all real credentials —
  a suite that runs slower than ~2s is doing network I/O and is wrong.
- Every new module needs at least a smoke test; every bugfix needs the
  regression test that would have caught it.
- MCP tools test through `app.call_tool`; Slack verbs through
  `handle_command`; graph nodes through `build_hunt_graph` with fakes.

## Where to start

`docs/roadmap.md` is the single source of planned work, with acceptance
criteria per item. Good first contributions:

- **Tier 0 (production hardening)** — well-scoped, high-impact items
  from the 2026-08 audit: cross-run dedup in candidate queries, advisory
  locks around billed runs, a Postgres-marked test suite, heartbeat +
  run/cost ledger, per-task model routing.
- **A new ingestion source** — the most self-contained change there is;
  recipe in `CLAUDE.md` section 10.
- **Agent tools** (`qualification/tools.py`) — each tool is one function,
  one registry entry, one smoke test.

## PR checklist

- [ ] `make test` green, `uv run ruff check` clean on touched files
      (CI enforces both on 3.11–3.13 plus a wheel install smoke —
      GitHub Actions and CircleCI run the same commands)
- [ ] Guardrails in `CLAUDE.md` section 4 respected
- [ ] Prompt changed materially? Version bumped
- [ ] New capability? Landed on Storage/runner first, all surfaces synced
- [ ] Docs updated in the same PR (`CLAUDE.md` rule: drift is worse than
      no docs)

## Releasing (maintainers)

One command, from a clean `main`:

```bash
make release VERSION=0.4.1     # or: make release BUMP=patch
```

It bumps `pyproject.toml`, re-runs lint and tests, commits, tags
`v0.4.1`, and pushes. The `publish` workflow then does the rest on its
own: lint + tests + wheel smoke in a clean venv, build, upload to PyPI,
and cut the GitHub release with the artifacts attached.

Auth is [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
— GitHub mints a short-lived OIDC token per run, so there is no API
token in repo secrets to leak or rotate. The publish job runs in the
`pypi` environment; adding a required reviewer there turns automatic
publishing into approve-then-publish.

PyPI versions are immutable — a bad `0.4.1` can only be yanked, never
replaced. That is why the workflow runs the full suite and installs the
built wheel before it uploads anything.
