# karani — developer aliases. The real interface is the `karani` CLI
# (see `karani --help`); these targets exist for muscle memory and CI.

.PHONY: test lint build ingest qualify digest daily hunt unschedule \
        autopilot actions funnel mcp slack hourly infra-up infra-up-llm \
        infra-down

test:
	uv run pytest tests -q

lint:
	uv run ruff check karani tests

build:
	uv build

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
