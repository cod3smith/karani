# karani — daily chained runs.
#
# Suggested cron (Nairobi timezone):
#   30 5,13 * * * cd /path/to/karani && make ingest
#   0  6    * * * cd /path/to/karani && make daily-digest
#
# `make daily` chains all three: ingest → qualify → digest.

PY ?= python
LIMIT ?= 50
DIGEST_LIMIT ?= 20
DIGEST_OUT ?= data/digest.html

.PHONY: help ingest qualify digest daily agent discover status stats sweep test mcp actions funnel \
        notify slack-listen infra-up infra-up-llm infra-down infra-logs infra-psql

help:
	@echo "targets:"
	@echo "  make ingest         # fetch + pre-filter + sweep"
	@echo "  make qualify        # single-turn qualify (up to LIMIT=$(LIMIT))"
	@echo "  make agent          # agent-mode qualify (LIMIT=5 by default)"
	@echo "  make digest         # render top qualified to $(DIGEST_OUT)"
	@echo "  make daily          # ingest + qualify + digest"
	@echo "  make daily-digest   # qualify + digest (skip ingest)"
	@echo "  make discover       # probe unpromoted companies"
	@echo "  make status         # pipeline funnel counts"
	@echo "  make sweep          # close stale jobs"
	@echo "  make test           # run pytest"
	@echo "  make mcp            # serve the MCP server over stdio"

ingest:
	$(PY) -m ingestion.cli run

qualify:
	$(PY) -m ingestion.cli qualify --limit $(LIMIT)

agent:
	$(PY) -m ingestion.cli qualify --agent --limit 5

digest:
	$(PY) -m ingestion.cli digest --limit $(DIGEST_LIMIT) --format html --output $(DIGEST_OUT)
	@echo "wrote $(DIGEST_OUT) — open it in a browser"

daily: ingest qualify digest

daily-digest: qualify digest

discover:
	$(PY) -m ingestion.cli discover --limit 10

status:
	$(PY) -m ingestion.cli stats

actions:
	$(PY) -m ingestion.cli actions

funnel:
	$(PY) -m ingestion.cli funnel

sweep:
	$(PY) -m ingestion.cli sweep

test:
	$(PY) -m pytest tests -q

mcp:
	$(PY) -m mcp_server

notify:
	$(PY) -m ingestion.cli notify --kind digest

slack-listen:
	$(PY) -m slackbridge

# --- infrastructure (docker compose) ---

infra-up:
	docker compose up -d db

infra-up-llm:
	docker compose --profile local-llm up -d

infra-down:
	docker compose down

infra-logs:
	docker compose logs -f

infra-psql:
	docker exec -it karani-db psql -U karani -d karani
