# karani — developer aliases. The real interface is the `karani` CLI
# (see `karani --help`); these targets exist for muscle memory and CI.

.PHONY: test test-pg lint build release ingest qualify digest daily hunt \
        unschedule autopilot actions funnel mcp slack hourly infra-up \
        infra-up-llm infra-down

test:
	uv run pytest tests -q

# Postgres-backed SQL coverage — needs `karani infra up` (skips if down).
test-pg:
	uv run pytest -m pg tests/test_pg_storage.py -q

lint:
	uv run ruff check karani tests

build:
	uv build

# Cut a release: bump, verify, tag, push. The publish workflow takes it
# from there (tests, build, PyPI via Trusted Publishing, GitHub release).
#   make release VERSION=0.4.1   or   make release BUMP=patch
release:
	@test -n "$(VERSION)$(BUMP)" || \
		{ echo "usage: make release VERSION=0.4.1 | BUMP=patch"; exit 1; }
	@test -z "$$(git status --porcelain)" || \
		{ echo "working tree is dirty — commit first"; exit 1; }
	@test "$$(git rev-parse --abbrev-ref HEAD)" = main || \
		{ echo "releases are cut from main"; exit 1; }
	uv version $(if $(VERSION),$(VERSION),--bump $(BUMP))
	uv run ruff check karani tests
	uv run pytest tests -q
	@v=$$(uv version --short); \
	 git diff --quiet || git commit -am "Release $$v"; \
	 git tag -a "v$$v" -m "karani $$v" && \
	 git push origin main && \
	 git push origin "v$$v" && \
	 echo "pushed v$$v — track the publish with: gh run watch"

ingest:
	uv run karani run

qualify:
	uv run karani qualify --limit 50

digest:
	uv run karani digest --limit 20 --format html --output data/digest.html

daily: ingest qualify digest

hunt:
	uv run karani hunt

unschedule:
	uv run karani unschedule

autopilot:
	uv run karani autopilot

actions:
	uv run karani actions

funnel:
	uv run karani funnel

mcp:
	uv run karani mcp

slack:
	uv run karani slack

hourly:
	uv run karani hourly

infra-up:
	uv run karani infra up

infra-up-llm:
	uv run karani infra up --profile local-llm

infra-down:
	uv run karani infra down
