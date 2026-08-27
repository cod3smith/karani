# Memory architecture

How karani retains context, updates it, and uses it for decisions. The
decision record is `docs/adrs/0009-memory-architecture.md`; this is the
operating manual.

## Design in one paragraph

Memory is three layers with one invariant. **L1** is deterministic ground
truth in Postgres (jobs, verdicts, outcomes, stages — plus a `memories`
ledger of distilled facts). **L2** is a semantic index over the ledger
(mem0 + pgvector, embeddings and extraction via local Ollama — zero token
cost). **L3** is working context: the handful of recalled facts injected
into a prompt at decision time, budgeted and provenance-tagged. The
invariant: **the ledger is the system of record; the semantic index is
derived and disposable.** Drop the vector collection and nothing is lost —
recall quality degrades to deterministic keyword matching until the index
is rebuilt.

## The three layers

### L1 — System of record (always on, deterministic)

Two kinds of ground truth:

- **Structured events** the pipeline already keeps: `user_verdict`,
  `outcome`, `stages`, `fit_score`, prompt versions. These feed the
  few-shot `<past_verdicts>` block and `funnel_stats` directly — they are
  memory, just not phrased as prose.
- **The `memories` ledger** (`memories` table): distilled, human-readable
  facts, one row each. Append-only with soft deletes
  (`deactivate_memory`), exact-duplicate writes dedupe to a timestamp
  touch. Schema: `kind`, `content`, `source`, optional `job_id` /
  `company` scope, `active`, timestamps.

Memory kinds:

| kind | example | typical writer |
| --- | --- | --- |
| `preference` | "User verdict 'skip' on Coinbase — Senior Eng (fit_score=82)" | auto, on `record_verdict` |
| `outcome` | "Application outcome 'offer' at GitLab for Senior Backend Engineer" | auto, on `record_outcome` |
| `company` | "GitLab's recruiter replied within three days" | manual / agent |
| `strategy` | "Data-platform positioning beat causal-ML angle for infra roles" | manual / agent |
| `question` | "PostHog's screen asked about incident ownership" | manual / agent (question bank) |

### L2 — Semantic index (mem0, optional)

`KARANI_MEMORY=mem0` layers mem0 OSS on top of the ledger. Every
non-deduped `remember()` also writes to mem0, which handles extraction,
conflict resolution, and embedding into **pgvector in the same Postgres**
(one database, one backup story). Recall becomes semantic search — "how
fast do handbook-first companies respond?" finds the GitLab fact without
sharing a keyword.

mem0's LLM and embedder default to local Ollama (`llama3.2:3b` +
`nomic-embed-text`, 768 dims — extraction is a small-model task), so
memory costs zero tokens. The vector store reads `MEM0_PG_URL`
(default: the compose Postgres on :5433) — deliberately separate from
`DATABASE_URL`, so the ledger can live in Neon while the disposable
index stays local. Every knob is env-overridable (`MEM0_*` in
`.env.example`); rebuild the index any time with
`python -m ingestion.cli reindex`.

Failure policy: any mem0 error (extra not installed, Ollama down, vector
store unreachable) logs a warning and degrades to `basic` — writes still
land in the ledger, recall falls back to deterministic matching. A memory
outage can never lose data or kill a qualify batch.

### L3 — Working context (per decision)

At decision time a `MemoryManager.recall_for_job(job_row)` builds a query
from company + title + role category, retrieves the top-k (default 5,
hard cap 10 in the prompt), and the prompt renders them as a `<memories>`
block with an explicit instruction: memories are context, they never
override a dealbreaker in the JD itself. This shipped as `qual-v2` /
`qual-agent-v2`, so memory-influenced qualifications are distinguishable
from pre-memory rows in `funnel_stats` — the memory layer's own impact is
A/B-measurable.

## Write paths (when memory updates)

1. **Verdict recorded** (CLI `verdict`, MCP `record_verdict`) →
   `remember_verdict()`: a `preference` fact with fit score, scoped to the
   company.
2. **Outcome recorded** (CLI `outcome`, MCP `record_outcome`) →
   `remember_outcome()`: an `outcome` fact — the strongest signal class.
3. **Deliberate teaching** (CLI `remember`, MCP `remember`): Kelyn or an
   orchestrating agent (ADAM) stores a fact directly. This is the API for
   "ADAM learned something about a company in another context and tells
   karani".
4. **Correction**: `Storage.deactivate_memory(id)` soft-deletes; write the
   corrected fact as a new row. History stays auditable.

## Read paths (when memory is consulted)

- **Qualification** (single-turn AND agent mode): `recall_for_job` per
  row, injected as `<memories>`. Complements `<past_verdicts>` — verdicts
  are dense structured pairs, memories are sparse distilled facts.
- **On demand**: CLI `recall`, MCP `recall` — ADAM's read API.
- **Planned** (roadmap 1.5.6/1.5.8): drafting and interview-prep consume
  `company` and `question` memories; follow-up drafting consumes
  `company` facts for a newsworthy hook.

## Scoping and ranking rules

- Company-scoped recall returns that company's facts PLUS unscoped ones
  (a global preference applies everywhere); other companies' facts are
  excluded.
- `basic` ranking: token overlap (stopword-filtered, word-anchored) with
  a boost when the memory's company appears in the query, recency as
  tiebreak. Deterministic — this is what tests run against.
- `mem0` ranking: vector similarity; mem0's own ADD/UPDATE/DELETE
  resolution keeps contradictory facts from accumulating.

## Modes and operations

```
KARANI_MEMORY=off | basic | mem0     # default: basic
```

- Runs with no infra: `basic` works on the in-memory Storage fallback.
- Full stack: `make infra-up-llm` (pgvector Postgres + Ollama),
  `uv sync --extra memory`, `KARANI_MEMORY=mem0`.
- Rebuild the semantic index after config changes: drop the
  `karani_memories` pgvector collection and replay the ledger
  (`SELECT content, kind, company FROM memories WHERE active`) through
  `MemoryManager.remember` — the ledger is the source of truth.

## What memory is NOT for

- Not a cache of job descriptions (jobs table already is one).
- Not auto-extracted from every LLM response — writes happen on explicit
  events or deliberate teaching, so junk can't accumulate silently.
- Not a place for secrets or credentials, ever.
- Not fine-tuning by another name — see roadmap non-goals. Memory is
  few-shot context plus measurement.
