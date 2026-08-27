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

.PHONY: help ingest qualify digest daily daily-digest daily-full daily-notify hourly hourly-legacy agent discover status stats sweep \
        test mcp actions funnel notify slack-listen schedule unschedule hunt autopilot \
        infra-up infra-up-llm infra-down infra-logs infra-psql

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

# Hourly hunt — one LangGraph pass (ADR 0013): ingest -> qualify ->
# autopilot -> notion -> report, per-node retry, Slack alert on errors.
# Falls back to the legacy make chain if langgraph isn't installed.
hourly:
	$(PY) -m orchestration --once || { [ $$? -eq 3 ] && $(MAKE) hourly-legacy PY="$(PY)"; }

hourly-legacy: ingest qualify
	-$(PY) -m ingestion.cli autopilot
	-$(PY) -m ingestion.cli notion sync

# Twice-daily summary pushes (digest + worklist) — deliberately NOT
# hourly; that would be spam.
daily-notify:
	-$(PY) -m ingestion.cli digest --limit 20 --format html --output data/digest.html
	-$(PY) -m ingestion.cli notify --kind digest
	-$(PY) -m ingestion.cli notify --kind actions

# One-shot full chain for manual runs.
daily-full: ingest qualify digest
	-$(PY) -m ingestion.cli autopilot
	-$(PY) -m ingestion.cli notify --kind digest
	-$(PY) -m ingestion.cli notify --kind actions
	-$(PY) -m ingestion.cli notion sync

autopilot:
	$(PY) -m ingestion.cli autopilot

# THE command: schedule the continuous hunt — hourly ingest -> qualify ->
# autopilot packs to Slack -> Notion board, plus twice-daily summaries.
hunt: schedule

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
	docker compose up -d db minio

infra-up-llm:
	docker compose --profile local-llm up -d

infra-down:
	docker compose down

infra-logs:
	docker compose logs -f

infra-psql:
	docker exec -it karani-db psql -U karani -d karani

# --- scheduling (macOS launchd) ---

PLIST_DAILY := $(HOME)/Library/LaunchAgents/com.karani.daily.plist
PLIST_HOURLY := $(HOME)/Library/LaunchAgents/com.karani.hourly.plist

schedule:
	mkdir -p logs $(HOME)/Library/LaunchAgents
	sed "s|__REPO__|$(CURDIR)|g" ops/karani.hourly.plist.template > $(PLIST_HOURLY)
	sed "s|__REPO__|$(CURDIR)|g" ops/karani.daily.plist.template > $(PLIST_DAILY)
	-launchctl unload $(PLIST_HOURLY) 2>/dev/null
	-launchctl unload $(PLIST_DAILY) 2>/dev/null
	launchctl load -w $(PLIST_HOURLY)
	launchctl load -w $(PLIST_DAILY)
	@echo "scheduled: com.karani.hourly (every hour, logs/hourly-*.log)"
	@echo "           com.karani.daily  (06:00 + 13:00 summaries, logs/daily-*.log)"
	@echo "run one now to verify: launchctl start com.karani.hourly"

unschedule:
	-launchctl unload -w $(PLIST_HOURLY) 2>/dev/null
	-launchctl unload -w $(PLIST_DAILY) 2>/dev/null
	rm -f $(PLIST_HOURLY) $(PLIST_DAILY)
	@echo "unscheduled"
