# 0009 · Ledger-first memory with mem0 as a derived index

**Status:** accepted

## Context

Karani's "learning" was scattered: few-shot verdict pairs in SQL, company
intel planned as a table, question banks as unstructured stage notes.
Kelyn wants the system to retain context, update it, and use it in
decisions — and wants mem0 on dedicated infrastructure (docker compose).

The tempting design is mem0-as-the-memory: write everything into it, let
its LLM extraction decide what's true. Rejected, because it makes an LLM
pipeline the system of record — non-deterministic writes, untestable
recall, and a hard dependency on a vector store + embedder for the
pipeline to function at all.

## Decision

Three layers with one invariant (full manual: `docs/memory.md`):

1. **L1 ledger** — a `memories` table in karani's own Postgres: distilled
   facts, deterministic writes, soft deletes, exact-dup coalescing. The
   system of record.
2. **L2 semantic index** — mem0 OSS over pgvector *in the same database*,
   LLM/embedder defaulting to local Ollama. Derived from the ledger;
   disposable and rebuildable.
3. **L3 working context** — top-k recalled facts injected per decision as
   a `<memories>` prompt block (prompt bumped to `qual-v2`, so memory's
   impact is A/B-measurable in `funnel_stats`).

Modes via `KARANI_MEMORY`: `off` / `basic` (deterministic recall, no
extra deps — the test substrate) / `mem0`. Any mem0 failure degrades to
`basic` with a warning: outages cost recall quality, never data and never
a batch.

Infrastructure: `docker-compose.yml` runs `pgvector/pgvector:pg16` (one
database for relational + vectors) and Ollama under an opt-in
`local-llm` profile. mem0 stays an in-process library — no memory
microservice to operate.

Writes happen on explicit events only (verdict, outcome, deliberate
`remember` by Kelyn or ADAM) — never auto-extracted from arbitrary LLM
output, so junk cannot accumulate silently.

## Consequences

- **Positive:** deterministic tests (basic mode on in-memory Storage);
  the pipeline runs with zero memory infra and upgrades in place.
- **Positive:** one Postgres for jobs + memories + vectors — one DSN, one
  backup, one compose service.
- **Positive:** ADAM gets a symmetric teach/ask API (`remember`/`recall`
  MCP tools), and memory-written qualifications carry a new prompt
  version for measurement.
- **Negative:** basic recall is keyword-dumb; paraphrased facts are only
  found in mem0 mode. Accepted — mem0 is the upgrade path, not a
  prerequisite.
- **Negative:** dual-write (ledger + mem0) can drift if mem0 writes fail.
  Accepted: the ledger wins by definition, and the index is rebuildable
  from it.
- **Neutral:** compose Postgres (localhost:5433) vs Neon is Kelyn's
  choice per environment — `DATABASE_URL` is the only switch.
