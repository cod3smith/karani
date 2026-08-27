# 0003 · OpenRouter default, Anthropic optional

**Status:** accepted (supersedes 0.1's Anthropic default)

## Context

The v0.1 qualifier used Anthropic's SDK directly (Claude Haiku). Two things
changed:

1. Kelyn wanted Kimi K2 Thinking (Moonshot AI), specifically for its
   extended thinking + tool-use capability. Kimi is on OpenRouter but not
   the direct Anthropic API.
2. OpenRouter's model catalog turns out to be enormous — Anthropic, OpenAI,
   Google, DeepSeek, Moonshot, Mistral, Meta — all behind one API and one
   API key. That's strictly more flexibility for a personal tool.

## Decision

Split the qualifier into provider-specific modules behind a common protocol:

- `qualification/client.py` — `QualifierClient` protocol + shared JSON
  extraction. Provider-agnostic.
- `qualification/openrouter.py` — default provider. Only depends on `httpx`.
- `qualification/anthropic.py` — optional provider. Import guarded so the
  Anthropic SDK becomes an optional dep (`pyproject.toml` `[anthropic]`
  extra).
- `qualification/factory.py` — `get_qualifier()` reads `QUAL_PROVIDER` env.

## Consequences

- **Positive:** Kimi K2 Thinking works out of the box; new provider is a
  few-hour add.
- **Positive:** Model flexibility. Kelyn can A/B a specific role's
  qualification against different models via `--model` without config
  changes.
- **Positive:** Fewer dependencies for the common case. Base install
  doesn't pull the Anthropic SDK.
- **Negative:** Anthropic-specific features (beta features, tool use
  particularities) become second-class. We accept this because OpenRouter
  is our default and drives feature priority.
- **Negative:** OpenRouter is a middleman with its own uptime. Mitigation:
  Anthropic direct is one env-var away.
