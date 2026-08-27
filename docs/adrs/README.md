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
