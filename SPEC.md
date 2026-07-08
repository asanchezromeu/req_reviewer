# ReqIQ — Target Product Specification & Migration Instructions

This document defines where the project is going. It complements `CLAUDE.md` (which describes the
repo as it exists today). When the two disagree about direction, this document wins. When working
on a task, read `CLAUDE.md` to understand the current code, and this file to understand what we are
building toward.

## 1. Product vision

An AI assistant for **requirements definition, management and verification** in systems engineering
(automotive context: ASPICE SYS.2, INCOSE quality rules). The end state is an **engine exposed as an
API**, designed to be embedded into a requirements-management tool we are developing separately.
Everything user-facing in this repo (the current React frontend) is a *consumer* of that API — a
test bench and demo client, never the product itself.

The engine runs on a **locally hosted LLM via Ollama**. Cloud providers (OpenAI, Anthropic, Gemini)
remain available as optional backends for experimentation and benchmarking, but the reference
deployment is fully local (the Raspberry Pi / on-prem model already described in `README.md`).
No requirement text may be sent to a cloud provider unless the operator has explicitly configured
a cloud provider — local is the default, not a fallback.

## 2. Core capabilities

### 2.1 Model adaptation ("training")

The engine must improve over time from material the user feeds it. "Training" here is deliberately
layered, from cheap to expensive. Implement and stabilize each tier before moving to the next:

**Tier 1 — Curated example & prompt library (exists, consolidate).** Good examples, bad examples,
and counter-examples of requirements, stored with labels and rationale. These feed few-shot context
into every LLM call. The backend already has training examples/datasets and a prompts library —
this becomes a first-class, API-exposed corpus, not a UI feature.

**Tier 2 — Reference knowledge bases (RAG).** Ingest reference material: existing requirement
baselines, standards excerpts, glossaries, project-specific terminology. Chunk, embed (Ollama
embeddings, `OLLAMA_EMBED_MODEL`), and store alongside the requirement store. All retrieval-backed
features (search, summary, review) draw from these indexes.

**Tier 3 — Fine-tuning (later phase, design for it now).** The example corpus from Tier 1 doubles
as a fine-tuning dataset. The pipeline is: export dataset in a standard format (JSONL
instruction/response pairs) → fine-tune externally (LoRA) → convert to GGUF → import into Ollama
via a Modelfile. The engine itself does not fine-tune; it *produces the dataset* and *consumes the
resulting model*. The existing "distillation jobs" concept maps here. Requirements on the engine:
dataset export endpoint, model registry (which Ollama model tag is active per feature), and
side-by-side evaluation of a candidate model against the current one on a held-out example set.

### 2.2 Advanced search

Input: a free-text question ("What is the nominal current of the ECU?"). Output: the requirement(s)
that answer it — or an honest "no requirement covers this."

Pipeline (this refines what `backend/showcase.py` already does):

1. **Retrieve** candidates using the blended scoring that exists today (embedding cosine similarity
   + keyword/phrase/structural scores + parameter penalty).
2. **Reason before answering.** This is the differentiating requirement: the top-scored match is a
   *candidate*, not the answer. A verification LLM step receives the question and the top-N
   candidates and must judge, for each, whether it actually answers the question. It returns a
   structured verdict: `answers` / `partially_answers` / `does_not_answer`, with a one-line
   justification each.
3. **Decide cardinality.** If exactly one candidate answers → return it. If several answer distinct
   facets of the question (e.g. nominal current in operation mode *and* in degraded mode) → return
   all of them, labeled by facet. If none answer → say so explicitly; never return the "least bad"
   match as if it were an answer.

The API response must carry the requirement IDs, the full requirement text, the verdicts, and the
justifications, so the consuming tool can render them however it wants.

### 2.3 Executive summary

Audience: PMs and managers not involved in development. Input: a free-text question. Output:
**prose, never a dump of requirement text.**

Pipeline: RAG retrieval over the requirement store (broad retrieval, the `broad_summary_sources` /
`select_summary_sources` logic is the starting point) → LLM synthesis with a system prompt that
enforces: plain language, no requirement verbatim quotes, quantitative facts allowed, and a closing
list of source requirement IDs for traceability (IDs only, not text). The non-LLM fallbacks
(`fallback_summary`, `summarize_quantitative_answer`) stay, as graceful degradation when Ollama is
unreachable.

Hard rule shared by 2.2 and 2.3: **the engine must not invent requirements.** Every factual claim
in a summary must be traceable to a retrieved requirement; if retrieval comes back empty or weak,
the answer is "the requirement set does not specify this," not a plausible guess.

### 2.4 Existing review features

The INCOSE 8-rule individual scoring, set-level consistency checks, and conflict detection from the
main app are kept and folded into the same engine as a third capability group ("verification").
They follow the same architectural rules below (API-first, provider-agnostic via `llm_complete`).

## 3. Target architecture

```
┌────────────────────────────┐     ┌──────────────────────────────┐
│  Requirements-mgmt tool    │     │  Web app (this repo,         │
│  (external, in development)│     │  demo/test client)           │
└──────────────┬─────────────┘     └──────────────┬───────────────┘
               │        HTTP / OpenAPI            │
               ▼                                  ▼
        ┌─────────────────────────────────────────────┐
        │            ReqIQ Engine  (FastAPI)          │
        │  /api/v1: search, summary, review, corpus,  │
        │           datasets, models, health          │
        ├─────────────────────────────────────────────┤
        │  Retrieval (embeddings + blended scoring)   │
        │  llm_complete → Ollama (default) / cloud    │
        │  Stores: requirements, examples, ref. KBs   │
        └─────────────────────────────────────────────┘
```

Architectural rules — these are the constraints every change must respect:

1. **API-first.** Every capability is reachable through a versioned REST API (`/api/v1/...`) with a
   complete OpenAPI schema. The React frontend calls only these public endpoints (via
   `frontend/src/lib/api.js`, which stays the single client). If a feature works in the UI but has
   no documented API route, the feature is not done.
2. **One engine.** The three parallel implementations converge into one. `backend/` is the base.
   `backend/showcase.py`'s retrieval/ranking becomes the core search module (rename away from
   "showcase"). The root Node prototype and the Streamlit prototype are frozen: no new work goes
   into them; salvage `src/conflict-precheck.mjs`'s heuristic conflict pre-pass (port to Python)
   and the deterministic baseline scoring from `req_analysis.py` (useful as a non-LLM fallback and
   as a regression oracle), then move both prototypes under `legacy/`.
3. **Provider-agnostic LLM layer.** `llm_complete(...)` remains the single dispatch point. New
   features must call it — never a provider SDK directly. Ollama is the default provider everywhere.
4. **Structured LLM I/O.** Every LLM call that feeds program logic requests JSON and passes through
   schema validation/repair (generalize the `validateReviewPayload` idea from the Node prototype
   into a Python contract module). Malformed LLM output must degrade to the deterministic fallback,
   never crash or silently pass through.
5. **Auth from day one, minimal.** Static API keys (env-configured, `Authorization: Bearer`) on all
   `/api/v1` routes. Enough for the integration story; real auth comes with the host tool.
6. **Storage stays swappable.** Keep the Mongo-or-in-memory pattern for app data; SQLite for the
   requirement/embedding store is fine and stays. Document which store owns what.

## 4. API surface (v1 sketch)

Claude Code: treat this as the contract to converge on; refine details in code review, don't
redesign the shape without discussion.

```
GET  /api/v1/health                       # engine + ollama + index status
GET  /api/v1/models                       # available providers/models, active model per feature

POST /api/v1/requirements/import          # bulk import (CSV/ReqIF/JSON)
GET  /api/v1/requirements                 # list/filter
POST /api/v1/index/rebuild                # trigger embedding refresh; GET status endpoint too

POST /api/v1/search                       # {query} → {matches:[{id,text,verdict,justification,facet?}], answered:bool}
POST /api/v1/summary                      # {query} → {summary_text, source_ids:[...], degraded:bool}

POST /api/v1/review/requirement           # INCOSE per-requirement scoring
POST /api/v1/review/set                   # set-level consistency + conflicts

POST /api/v1/corpus/examples              # add good/bad example with label + rationale
GET  /api/v1/corpus/examples
POST /api/v1/corpus/references            # ingest reference KB material
POST /api/v1/datasets/export              # JSONL export for fine-tuning
POST /api/v1/models/evaluate              # candidate vs. active model on held-out examples
```

## 5. Migration plan (ordered)

Work in this order; each phase should leave the repo working and tested.

**Phase 0 — Repo hygiene.** Initialize git. Move Node and Streamlit prototypes to `legacy/` with a
README explaining what was salvaged from each. Update `CLAUDE.md` accordingly.

**Phase 1 — API consolidation.** Introduce `/api/v1` with auth, migrate existing routes into it
(keep old `/api` routes as thin deprecated aliases until the frontend is switched), generate and
commit the OpenAPI spec. Rename/refactor `showcase.py` into the core retrieval module. Switch
`lib/api.js` to `/api/v1`.

**Phase 2 — Advanced search v1.** Add the verification-reasoning step and cardinality logic on top
of existing retrieval. Add the structured-output contract module. Tests: a fixture set of
requirements with question→expected-IDs pairs, including "multiple facets" and "no answer" cases.

**Phase 3 — Executive summary v1.** Enforce the prose-only, traceable-sources output contract.
Tests assert: no verbatim requirement sentences in the summary, source IDs present, degraded mode
works with Ollama down.

**Phase 4 — Training tiers 1–2.** Corpus endpoints, few-shot injection into search/summary/review
prompts, reference-KB ingestion into retrieval.

**Phase 5 — Training tier 3 scaffolding.** Dataset export, model registry, evaluate endpoint.
Actual fine-tuning runs happen outside this repo.

## 6. Testing & quality bar

Every phase adds tests runnable without a live Ollama (mock `llm_complete`; keep the existing
stdlib-only unit-test pattern). Retrieval quality gets a small benchmark fixture (requirements +
questions + expected answers) run in CI-style via pytest, so ranking changes are measurable, not
vibes. The deterministic scorers from the Streamlit prototype serve as regression oracles for the
review features.

## 7. Open decisions (ask before assuming)

- Import formats for the host tool integration: ReqIF? DOORS CSV export? Confirm before building
  importers beyond CSV/JSON.
- Multi-project/tenant separation in the stores: needed for the host tool, or single-project per
  deployment instance?
- Streaming responses (SSE) for summary generation in the API, or request/response only?
- Where fine-tuning runs (Tier 3): which external pipeline/hardware, and which base model family —
  this affects the dataset export format.
