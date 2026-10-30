# Architecture Decision Records

Each ADR captures one meaningful decision: context, options considered, choice
made, consequences. Format kept deliberately terse.

**Rules:**
- Never edit an accepted ADR to reverse it. Add a new one that supersedes.
- Reference ADR numbers from code comments where the decision matters.
- Numbering: `NNNN-slug.md`, zero-padded, monotonic.

## Index

- [0001](0001-tiered-filter-not-single-llm-pass.md) — Tiered filter (deterministic → LLM → agent) instead of one LLM pass
- [0002](0002-sf-band-global-remote-positioning.md) — Positioning: SF-band global remote, not Kenya-specific
- [0003](0003-openrouter-default-anthropic-optional.md) — OpenRouter default, Anthropic optional
- [0004](0004-normalized-content-hash.md) — Normalized content hash + canonical hash for dedup
- [0005](0005-state-machine-columns-not-table.md) — Application state machine as columns, not a separate table
- [0006](0006-in-memory-storage-fallback.md) — In-memory storage fallback as a real code path
- [0007](0007-agent-mode-optional-not-default.md) — Agent mode is opt-in, not default
- [0008](0008-mcp-server-interface.md) — MCP server as the interactive interface layer
- [0009](0009-memory-architecture.md) — Ledger-first memory with mem0 as a derived index
- [0010](0010-slack-two-way-surface.md) — Slack as the two-way daily surface (Socket Mode bridge)
- [0011](0011-notion-mirror.md) — Notion board as a karani-owned mirror, not a second store
- [0012](0012-autopilot-draft-human-send.md) — Autopilot drafts, human sends
- [0013](0013-langgraph-background-orchestration.md) — LangGraph for background orchestration — and nowhere else
- [0014](0014-artifact-store-and-humanizer.md) — Per-job artifacts in object storage + a measured humanizer pass
- [0015](0015-namespace-config-pypi.md) — One namespace, one config file, one CLI — pip-installable
- [0016](0016-tier0-production-hardening.md) — Production hardening: dedup, locks, ledger, verify gate
- [0017](0017-pluggable-llm-providers.md) — Pluggable LLM providers, local-first qualification
- [0018](0018-out-of-process-liveness.md) — Liveness must live outside the process it watches
- [0019](0019-hosted-tiers-behind-a-budget.md) — Hosted Claude tiers behind a monthly allocation
- [0020](0020-submission-ready-documents.md) — Submission-ready documents: ATS-plain .docx, PDF for humans
- [0021](0021-computational-bio-is-in-scope.md) — Computational biology is in scope; exclusions become liftable
- [0022](0022-conversion-ledger-and-variant-promotion.md) — A conversion ledger, and promotion that refuses on noise
- [0023](0023-submission-console-and-write-back.md) — A static submission console, and a write-back that creates labels
- [0024](0024-prep-on-the-state-change.md) — Interview prep fires on the state change
- [0025](0025-every-pass-has-a-deadline.md) — Every pass has a deadline
