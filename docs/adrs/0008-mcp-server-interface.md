# 0008 · MCP server as the interactive interface layer

**Status:** accepted

## Context

The pipeline was CLI-only: `python -m ingestion.cli <cmd>`. That works for
cron and one-off terminal use, but the daily human loop — review the
shortlist, dig into one role, draft, record a verdict — is conversational
by nature, and Kelyn lives in MCP clients (Claude Code, Cowork). Roadmap
item 1.3 also needed a delivery surface beyond an HTML file on disk.

Options considered: a web dashboard (Streamlit — Tier 6, deferred), a Slack
bot, or an MCP server exposing the existing runner functions.

## Decision

Add `mcp_server/` — an MCP server on the official `mcp` Python SDK (2.x,
`MCPServer`), stdio transport, exposing tools that cover every CLI verb:
`ingest`, `sweep`, `discover`, `qualify`, `digest`, `shortlist`, `get_job`,
`pipeline_stats`, `next_actions`, `funnel_stats`, `draft`, `record_verdict`,
`set_status`, `add_stage`, `record_outcome`, `remember`, `recall`.

Key structural choices:

1. **The server is a thin adapter.** Tools call the same functions the CLI
   calls (`orchestrator.run`, `qualify_pending`, `draft_for_job`, `Storage`
   methods). No business logic lives in `mcp_server/` — if a tool needs a
   query, the query goes on `Storage` (that's why `Storage.get_job` was
   promoted out of the CLI).
2. **Process-wide Storage singleton, lazily connected.** The in-memory
   fallback only works if state survives across tool calls; a per-call
   connect would silently discard everything between calls. Tests inject
   via `use_storage()`.
3. **Seams for LLM and resume.** `_make_qualifier` and `_load_resume` are
   module-level functions so tests (and embedders) can swap in fakes
   without touching env vars or `data/resume.md`.
4. **Expected failures raise `ToolError`.** The 2.x SDK masks arbitrary
   exceptions with a generic message; `ToolError` passes the message
   through. Bad job id, invalid verdict/status/outcome, bad digest format
   are user-input errors the client should see verbatim.
5. **SDK pinned `mcp>=2`.** We build against the 2.x API
   (`mcp.server.mcpserver.MCPServer`); a 1.x resolution would break at
   import (`FastMCP` rename).

## Consequences

- **Positive:** the daily loop is drivable from any MCP client; the
  project's own `.mcp.json` auto-wires it into Claude Code sessions.
- **Positive:** `server.call_tool` gives tests the full validation +
  execution + serialization path with no transport, so the MCP surface is
  cheap to cover deterministically.
- **Negative:** `qualify` and `draft` are billed operations now reachable
  by an LLM client. Mitigation: tool docstrings say "billed" explicitly and
  defaults are conservative (`limit=20`, `agent_mode=False`).
- **Negative:** one more surface to keep in sync with the CLI. Mitigation:
  both are thin over the same runners; new verbs should land in `Storage`/
  runners first, then both surfaces.
- **Neutral:** stdio only for now. The SDK supports streamable-http if a
  remote/hosted mode is ever needed.
