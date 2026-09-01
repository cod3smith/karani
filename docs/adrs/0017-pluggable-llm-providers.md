# ADR 0017 — Pluggable LLM providers, local-first qualification

Date: 2026-09-01
Status: accepted
Extends: 0015 (config file), 0007 (provider split)

## Context

Two pressures converged:

1. **The OpenRouter outage.** A rotated-but-not-installed API key made
   every qualification call fail 401 for days. Qualification is the
   pipeline's bulk tier — ~50 LLM calls per hourly pass — and it was
   hard-wired to a single hosted provider. One dead key stalled the
   entire hunt.
2. **Open-sourcing.** karani is now `pip install karani` for anyone.
   "Anyone" has whatever LLM access they have: an OpenAI key, an
   Anthropic key, a Groq free tier, or just a laptop running Ollama.
   A provider list frozen into an if-chain in `factory.py` makes every
   new provider a karani code change.

The existing structure was close: `OpenRouterQualifier` already spoke
the generic OpenAI Chat Completions wire format, and `LocalQualifier`
subclassed it. But the inheritance was backwards (the generic client
was named after one vendor), there was no OpenAI-proper provider, and
no way to point at an arbitrary compatible endpoint from config.

## Decision

1. **`OpenAICompatQualifier` is the provider base**
   (`qualification/openai_compat.py`): the full Chat Completions
   client — retry, JSON response_format, truncation warning, usage
   recording, and `chat_turn` (tool calling / agent mode). Subclasses
   override two hooks: `_extra_headers()` and `_extra_payload()`.
   - `openrouter.py` — attribution headers + `reasoning.effort`.
   - `openai.py` — api.openai.com, key required; `base_url` +
     `api_key_env` make it cover every OpenAI-compatible cloud
     (Groq, Together, Mistral, DeepSeek, a remote vLLM).
   - `local.py` — localhost, placeholder key, generous timeout.
   - `anthropic.py` stays separate (different wire format, optional
     SDK).

2. **The factory is a registry.** `register_provider(name, builder)`
   replaces the if-chain. A builder takes a resolved `ProviderSpec`
   (model, effort, max_tokens, base_url, api_key_env, timeout — merged
   argument > env > `[llm.<task>]` > `[llm.default]` > built-in) and
   returns anything satisfying the `QualifierClient` protocol
   (`complete()`, plus `chat_turn()` for agent mode). Third parties
   plug in without touching karani.

3. **Secrets stay out of toml.** Custom endpoints name their key's env
   variable via `api_key_env`; the factory reads the value from the
   environment at build time. This preserves the ADR 0015 rule.

4. **Qualification defaults to local in the reference config.** The
   example toml (and Kelyn's live config) route `[llm.qualify]` and
   `[llm.agent]` to Ollama (`qwen3:4b` — tool calling + thinking,
   fits on a laptop). Zero token cost for the bulk tier, and no hosted
   outage can stall the hunt. Drafting stays on a strong hosted model
   by default: the pack is what gets the interview, and it runs a few
   times per day, not fifty times per hour. The *built-in* fallback
   (no config at all) remains openrouter, because we cannot assume a
   local server exists on a fresh install.

## Consequences

- Adding a hosted OpenAI-compatible provider is now zero karani code:
  `provider = "openai"` + `base_url` + `api_key_env` in karani.toml.
- Adding a genuinely different wire format is one module + one
  `register_provider` call.
- Local qualification quality is bounded by the local model. qwen3:4b
  scores conservatively next to Kimi K2; the `fit_score` distribution
  shifts, so autopilot's `min_fit` floor may need retuning after a few
  passes (watch `funnel_stats`). `prompt_version` + `model` columns on
  QualificationResult already distinguish the cohorts.
- The `verify` branch (ADR 0016) becomes free when the agent task is
  local — the "billed" caveat in its config comment no longer applies
  under local routing.
- Tests mock at `openai_compat.httpx` — one seam for all compat
  providers.
