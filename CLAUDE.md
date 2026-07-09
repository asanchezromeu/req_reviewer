# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

`SPEC.md` is the forward-looking companion to this file: it defines the target product (an
API-first requirements-review engine) and the ordered migration plan. When the two disagree about
direction, `SPEC.md` wins — read it before starting any non-trivial change.

This repo is converging **two independent implementations** into one engine, plus a `legacy/`
directory holding two frozen prototypes:

| Implementation | Location | Stack | Status |
|---|---|---|---|
| **Main app / engine** | `backend/` + `frontend/` | FastAPI + React (CRA/craco, shadcn/radix) | Actively developed — the one engine SPEC.md converges on |
| Node/Ollama prototype | `legacy/node-prototype/` | Plain Node `http`, no deps, no build | Frozen — see `legacy/README.md` |
| Streamlit prototype | `legacy/streamlit-prototype/` | Streamlit + pandas | Frozen — see `legacy/README.md` |

`legacy/README.md` documents what each frozen prototype is and exactly what was salvaged from each
into `backend/` (the heuristic conflict pre-check and the deterministic non-LLM scorer). Do not add
new features to anything under `legacy/`.

## Commands

### Main app — backend (FastAPI)

```bash
pip install -r backend/requirements.txt
uvicorn backend.server:app --reload --host 0.0.0.0 --port 8000   # run from repo root
```

Backend tests:

```bash
python -m unittest backend.tests.test_retrieval backend.tests.test_conflict_precheck \
  backend.tests.test_deterministic_review backend.tests.test_auth backend.tests.test_llm_contract \
  backend.tests.test_review_fallback backend.tests.test_search_verification \
  backend.tests.test_summary_contract backend.tests.test_corpus_fewshot \
  backend.tests.test_reference_kb backend.tests.test_reference_ingestion \
  backend.tests.test_model_registry backend.tests.test_dataset_export \
  backend.tests.test_model_registry_routes backend.tests.test_model_evaluate \
  backend.tests.test_testgen_context backend.tests.test_testgen_generation \
  backend.tests.test_testgen_resolve -v
  # ^ all stdlib+fastapi TestClient only, no live server or Ollama needed (LLM calls are mocked).
pytest backend/tests/test_reqiq_api.py     # hits a running server; BASE_URL/REACT_APP_BACKEND_URL env, defaults to http://localhost:8000
pytest backend/tests/test_reqiq_iter2.py
python -m backend.export_openapi           # regenerate backend/openapi.json after any route change
```

### Main app — frontend (React)

```bash
cd frontend
npm install
npm start        # dev server on :3000, proxies /api to :8000 (see package.json "proxy")
npm run build    # outputs frontend/build; FastAPI auto-serves it from "/" if the directory exists
npm test         # craco test
```

### Node/Ollama prototype (frozen, `legacy/node-prototype/`)

```bash
cd legacy/node-prototype
npm start   # runs server.mjs on :3000 (PORT env), serves public/ and proxies review requests to Ollama
node --test test/parse-requirements.test.mjs   # bare `npm test` finds 0 tests on newer Node — run explicitly
```

### Streamlit prototype (frozen, `legacy/streamlit-prototype/`)

```bash
cd legacy/streamlit-prototype
streamlit run main.py
python -m unittest tests.test_req_analysis -v
```

## Architecture — main app (`backend/` + `frontend/`)

**`backend/server.py`** is the FastAPI entrypoint. Key things to know:

- **API surface**: every route is registered once (no per-route prefix) and mounted twice — at
  `/api/v1` (authed, see below) and at `/api` (unauthenticated, `deprecated=True` in the OpenAPI
  schema — a thin alias kept only until every consumer has moved to v1, per `SPEC.md` Phase 1). The
  frontend (`frontend/src/lib/api.js`) talks to `/api/v1` only.
- **Auth**: `backend/auth.py`'s `require_api_key` dependency is applied to the `/api/v1` mounts. It
  checks `Authorization: Bearer <key>` against the comma-separated `API_KEYS` env var; if `API_KEYS`
  is unset, auth is disabled with a one-time startup warning (same graceful-degradation spirit as
  the Mongo-or-memory storage fallback below).
- **One requirements collection**: there is no "requirement sets" concept anymore. `backend/retrieval.py`'s
  `RequirementStore` (SQLite) is the single canonical store, shared between the requirements/search/summary
  routes (defined in `retrieval.py`) and the review/classify/ask routes (defined in `server.py`, which pulls
  the same store/indexer instances off the router returned by `create_requirements_router` — see the
  bottom of `server.py`). `POST /review/set`, `POST /classify`, `POST /ask` all operate over *whatever
  requirements are currently stored*, no id/scoping parameter.
- Persistence for everything else (system prompts, training examples/datasets, distillation jobs) is
  MongoDB via `motor`, but only if `MONGO_URL` is set. Otherwise `MemoryDatabase` / `MemoryCollection`
  (defined at the top of the file) provide an in-memory shim with the same async method signatures
  (`insert_one`, `find`, `update_one`, `delete_one`, ...), so the rest of the code is written once
  against a Mongo-like interface regardless of backend.
- `llm_complete(...)` is the single dispatch point for all LLM calls, branching on a `provider`
  string (`ollama`, `openai`, `anthropic`, `gemini`), each reading its own API key from env
  (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) / `OLLAMA_URL`. `AVAILABLE_MODELS` lists
  the selectable model IDs per provider surfaced to the frontend via `GET /api/v1/models`.
- If `frontend/build` exists, it's mounted at `/` via `StaticFiles`, so a built frontend and the API
  are served from the same FastAPI process (the Raspberry Pi deployment model described in
  `README.md`). During development the frontend runs separately on :3000 instead.
- `backend/incose_rules.py` holds the INCOSE 8-rule definitions plus the system prompts used for
  individual requirement scoring, set-level consistency checks, and the plain-language summarizer.
- `backend/conflict_precheck.py` is a standalone, tested utility salvaged from the frozen Node
  prototype (see `legacy/README.md`) — a heuristic conflict pre-check. Not wired into any route yet;
  that wiring is future work.
- `backend/deterministic_review.py` (salvaged from the frozen Streamlit prototype) is wired in as of
  Phase 2: `analyze_one`'s `deterministic_fallback_review(...)` uses it to degrade gracefully when
  the review LLM returns unparsable JSON — a real heuristic score instead of a hardcoded zero. This
  was directly motivated by live testing: `gemma3:1b` returns malformed JSON on a meaningful fraction
  of review calls.
- `backend/llm_contract.py` is the "structured LLM I/O contract" module (`SPEC.md` Phase 2),
  generalizing the legacy `review-contract.mjs` idea: `extract_json` (tolerant JSON extraction,
  `parse_json_strict` is now a thin re-export of it) and `reconcile_by_id` (schema-agnostic
  id-anchored reconciliation — every expected id gets exactly one result, synthesizing a fallback via
  a caller-supplied function when the model omitted or invalidated one). Used by both the
  review-fallback retrofit above and the search verification step below.
- `backend/openapi.json` is a committed snapshot of the OpenAPI schema — regenerate it with
  `python -m backend.export_openapi` whenever routes change.
- **Corpus examples + few-shot (Phase 4 Tier 1, `SPEC.md` 2.1)**: `POST/GET/DELETE /corpus/examples`
  (renamed from `/training/examples`, old path kept as a thin deprecated alias) is the curated
  good/bad example corpus. `get_fewshot_prefix()` (mirrors the existing `get_tailoring_prefix(...)`
  pattern) formats up to 3 good + 3 bad examples into a short calibration block, prepended to
  `analyze_one` and the set-level consistency check. It's deliberately flat/uncapped-by-relevance —
  Tier 1 is the curated corpus, not semantic retrieval (that's Tier 2, below). `retrieval.py`'s
  `/search` and `/summary` have no DB access, so `create_requirements_router` takes one more
  optional injected callable, `fetch_fewshot_examples`, following the same DI pattern as
  `llm_complete`; `server.py` passes `get_fewshot_prefix` directly.
- **Training tier 3 scaffolding (Phase 5, `SPEC.md` 2.1)** — `backend/model_registry.py` holds the
  pure helpers; `server.py` wires storage. The engine does not fine-tune; it produces the export and
  evaluates whatever model comes back from an external LoRA run:
  - `POST /datasets/export` turns the `/corpus/examples` corpus into a JSONL fine-tuning dataset
    (`model_registry.build_training_row`), reusing `analyze_one`'s exact user-message shape so the
    export matches the real review call's I/O. Target scores/fixes are **not invented** — they come
    from running `deterministic_review.review_requirement` on the corrected (or original) text, the
    same heuristics already used as the review fallback/oracle elsewhere.
  - `GET/PUT /models/registry[/{feature}]` — "which model is active per feature"
    (`review`/`search`/`summary`/`classify`/`ask`), backed by a new `db.model_registry` collection.
    **Deliberately not wired as an implicit default** into `ReviewRequirementBody` etc. — every
    existing endpoint still requires explicit `provider`/`model`; the registry is metadata other
    tools (and `/models/evaluate` below) read, not a fallback-resolution mechanism. Revisit if/when
    the host-tool integration wants implicit defaults.
  - `POST /models/evaluate` runs a candidate model against the registry's active ("review" only,
    for now — other features don't have a held-out question→answer format yet) model over the
    corpus, via `analyze_one`, **without** few-shot injection (isolates model capability from
    corpus-augmentation effects). Reports average score, accuracy
    (`model_registry.score_matches_expectation` against each example's good/bad label), and
    `degraded_count` — how often each model's result fell back to the deterministic scorer via
    `analyze_one`'s existing `fallback` marker, so a misleadingly-similar comparison caused by both
    models hitting that fallback is visible rather than hidden.
- **Test-case generation, Phase 6 (`SPEC-ADDENDUM-A.md`), all stages A–C complete** — project test
  context (A.2), category-aware generation with the two-verdict sufficiency gate (A.3–A.4), and the
  resolve/regenerate loop (A.5).
  - Confirmed decisions (previously defaulted, now settled): a test case's `requirement_ids` is a
    list (many-to-many capable — the engine does not auto-group requirements into one test case,
    but the schema supports a human doing so during review); the addendum's 7 categories are the
    built-in defaults but every category's guidance is stored and user-editable, and new category
    names can be added; Safety-related requirements always generate with `review_flags: ["safety"]`
    rather than being excluded.
  - `POST /testgen/context/analyze` sends every stored requirement plus any ingested reference
    chunks to the LLM (`backend/testgen_prompts.py`'s `CONTEXT_ANALYSIS_PROMPT`) and gets back
    `items` (operating modes, parameters+tolerances, interfaces, environmental conditions,
    terminology, categories present — each tagged with `source_requirement_ids`) and `questions`
    (things it couldn't infer, each with a `reason`). On malformed LLM output it degrades to an
    empty-items context with one system question flagging the failure — never crashes, never
    fabricates items. Storage is `db.test_context_versions`: every analyze/patch call **inserts a
    new version** rather than mutating in place (`get_current_test_context` reads the highest
    `version`), so old versions are retained — a generated test case records which context version
    it was built against. `GET /testgen/context` (404 until the first analyze), `PATCH
    /testgen/context` (apply `item_updates`/`new_items`/`question_answers`/`item_removals`, bump the
    version — answering a question also appends a derived context item; `item_removals` exists
    because the analysis LLM call can and does fabricate items with no basis in the actual
    requirement set - e.g. inventing an interface or a tolerance never stated anywhere - and
    `item_updates` alone can't discard a fabricated item, only correct its value), `GET
    /testgen/context/questions` (optional `?status=open|answered` filter).
  - `GET/PUT/DELETE /testgen/category-strategies[/{category}]` — same "defaults + override" pattern
    as `/models/registry` (Phase 5): `backend/testgen_prompts.py`'s `DEFAULT_CATEGORY_STRATEGIES`
    (the 7 built-ins, transcribed from the addendum's A.3 table) plus any stored override/custom
    category in `db.category_strategies`. `DELETE` reverts a built-in to its default text, or
    removes a fully custom category.
  - `POST /testgen/generate` (`requirement_ids: Optional[List[str]]`, defaults to every stored
    requirement; bounded `asyncio.Semaphore(8)` concurrency, same pattern as `review_set_endpoint`/
    `evaluate_model`) — two LLM calls per requirement, not three, plus a deterministic self-review
    pass. Requirements have no stored `category` field (`Requirement` in `retrieval.py` is just
    `id/text/source`), so classification happens on the fly every call. Both `/testgen/generate` and
    `/testgen/resolve` share one pipeline, `_generate_for_requirement` (`backend/server.py`): call 1
    (`CLASSIFY_AND_ASSESS_PROMPT`) combines classification and **Verdict 1** in one judgment — given
    the requirement, the known categories + their strategy text, the current project test context,
    and any authorized assumptions for this attempt, returns `{category, sufficient, gaps}`. If
    insufficient, each gap is persisted to `db.test_gaps` (`gap_source: "sufficiency"`) and returned
    with a `gap_id` — no test case generated. If sufficient, call 2 (`GENERATE_TEST_CASE_PROMPT`)
    generates `{preconditions, steps, acceptance_criteria, verification_method}` — the category's
    strategy text itself tells the model what shape to produce (e.g. Non-testable-by-test's strategy
    says to propose a verification method and a checklist instead of test steps), so **one
    schema/prompt covers testable and non-testable categories with no hardcoded Python branch for
    that case**. **Verdict 2** then runs `backend/testgen_lint.py`'s `check_anti_genericity` — a
    *deterministic* lint pass (no third LLM call, per A.7's "this is itself testable" framing),
    reusing `deterministic_review.py`'s `WEAK_WORDS`/`_has_measure` — that flags banned generic
    phrasing, acceptance criteria missing a measurable quantity (only enforced when the requirement
    or context actually establishes one — a purely functional/logging requirement can be satisfied
    by an observable-state criterion instead), and numeric values in the output that aren't
    traceable (by bare numeral, not exact wording) to the requirement, a context item, or a marked
    assumption. A Verdict-2 failure persists gaps the same way as Verdict 1 (`gap_source:
    "self_review"`) and the draft test case is discarded, not persisted. Both LLM calls degrade to
    `needs_input` (gaps persisted) on malformed JSON, never crash. Generated test cases persist to
    `db.test_cases` with `assumptions` populated only via an authorized fill-in (never silently);
    batch results are a mixed `generated`/`needs_input` list per requirement — one failure never
    blocks the rest.
  - `GET /testgen/testcases` (optional `?requirement_id=` filter).
  - `POST /testgen/resolve` (`{gap_id, resolution_type: "answer"|"authorize_fill", answer?}`) closes
    the loop A.5 calls for. `"answer"` appends a new `user_provided` context item from the answer
    text and bumps the context version (same mechanism as `PATCH /testgen/context`'s question-answer
    handling — creates version 1 if no context exists yet), so the fact benefits every future
    generation, not just this one. `"authorize_fill"` calls a new `AUTHORIZE_FILL_PROMPT` asking for
    exactly one proposed `{value, rationale, confidence}` for that specific gap only; on malformed
    output it does **not** fabricate a value — it returns `needs_input` with a fresh gap explaining
    the auto-fill failed rather than silently inventing one. On success the proposal becomes an
    `extra_assumptions` entry threaded through `_generate_for_requirement`, which is what actually
    populates `TestCase.assumptions` for the first time. Both paths mark the original gap
    `status: "resolved"` and regenerate via the shared pipeline. 404 on an unknown `gap_id`, 409 if
    already resolved. **Known simplification**: resolving one gap doesn't auto-close sibling gaps
    from the same requirement's earlier attempt — treat `GET /testgen/gaps?status=open` as the
    source of truth after each resolve.
  - `GET /testgen/gaps` (optional `?status=open|resolved` filter) — not in A.5's literal endpoint
    list, added because gaps are persisted objects now (needed for `/testgen/resolve` to have
    something to reference) and a host UI needs a way to discover outstanding `gap_id`s.

**`backend/retrieval.py`** (renamed from `showcase.py` in Phase 1 — this is now "the" requirements
store, not a demo-specific module) is self-contained: its own SQLite store, embeddings, and search
ranking, exposed via `create_requirements_router(llm_complete)`:

- `RequirementStore` persists requirements to SQLite (`backend/data/requirements.db` by default,
  override with `REQUIREMENTS_DB_PATH`).
- `IndexCoordinator` schedules a background embedding refresh (via Ollama's embeddings API,
  `OLLAMA_EMBED_MODEL`, default `embeddinggemma`) after every save/import, and exposes `status()` for
  polling from the frontend (`GET /index/status`, or trigger one explicitly with `POST /index/rebuild`).
- Retrieval blends multiple signals — cosine similarity on embeddings plus `keyword_score`,
  `phrase_score`, `structural_score`, and `parameter_penalty` — in `ranked_matches`/`score_requirement`.
- `POST /summary` (`broad_summary_sources` for broad queries, otherwise the top-N via
  `select_summary_sources`) retrieves several requirements and asks the local LLM (`ollama_summary`,
  system prompt in `backend/summary_prompts.py`) for an executive summary, with `fallback_summary`/
  `summarize_quantitative_answer` as non-LLM fallbacks (`degraded: true`, `degraded_reason` set, when
  the LLM path wasn't used, failed, or was used but rejected). Phase 3 (`SPEC.md` 2.3) added contract
  enforcement: `summary_violates_contract` checks the LLM's prose for verbatim-quoted requirement
  sentences (quantitative facts like numbers/units may be restated; this is deliberately the only
  hard-gated rule — the prompt also asks for a closing `Sources: <ids>` line and honest "not
  specified" answers, but those aren't gated the same way, since the structured `source_ids` field
  already guarantees traceability regardless of the prose). A violation discards the LLM's answer and
  falls back to the same deterministic path used when the LLM is unreachable.
- `POST /search` (Phase 2: `SPEC.md` 2.2 "advanced search") is retrieve-then-verify, not just
  ranking: `ranked_matches` produces candidates, then `verify_candidates` (using
  `backend/search_prompts.py`'s `SEARCH_VERIFICATION_PROMPT` via `llm_complete`, provider-agnostic)
  judges each one as `answers` / `partially_answers` / `does_not_answer` with a justification and an
  optional `facet` label, reconciled through `llm_contract.reconcile_by_id` so every candidate gets a
  verdict even if the model's response was incomplete. Cardinality logic then decides the top-level
  `answered`/`requirement` fields: exactly one `answers`/`partially_answers` candidate → that one;
  several (e.g. different operating modes) → all of them, `requirement: null`, distinguished by
  `facet`; none → `answered: false` with an honest message — the response always includes every
  candidate + its verdict for transparency, never fabricates an answer. If the verification LLM call
  itself fails, the response degrades to similarity-only matches with `unverified: true` rather than
  failing the request.
- **Reference KB (Phase 4 Tier 2, `SPEC.md` 2.1)**: `POST /corpus/references` (`{document, title,
  text}`) chunks (`backend/reference_kb.py`'s `chunk_reference_text` — paragraph-split, then
  sentence-windowed for oversized paragraphs) and embeds reference material (standards excerpts,
  glossaries) into two more tables on the *same* SQLite file as requirements —
  `reference_chunks`/`reference_embeddings` on `RequirementStore`. Ingestion is **additive**
  (`add_reference_chunks`), unlike requirements' full-replace `PUT` — ingesting one more standard
  shouldn't wipe earlier ones. A second `IndexCoordinator` (`reference_indexer`, exposed on the
  router alongside `indexer`) runs the same background-embedding machinery against the reference
  methods; `IndexCoordinator` itself was generalized with optional `stale_fn`/`save_fn`/`count_fn`
  constructor params to make this possible without duplicating the threading/status code.
  `GET /corpus/references` lists chunks; `GET /corpus/references/index/status` reports indexing
  progress separately from `/index/status` (requirements). Retrieval integration: `/search` and
  `/summary` each rank indexed reference chunks against the query vector
  (`reference_kb.rank_reference_chunks`, cosine-only — reference prose isn't "shall" statements, so
  the requirement-tuned keyword/structural scoring doesn't apply) and inject the top few as a
  labeled "REFERENCE MATERIAL" context block in the LLM call — grounding/context only, never treated
  as an answer candidate (no verdict, doesn't affect search cardinality).

**Frontend** (`frontend/src/`):

- `App.js` is a single route (`/`) rendering `pages/Dashboard.jsx`, which composes the feature tabs:
  `components/AnalyzeTab.jsx`, `TailoringTab.jsx`, `TrainingTab.jsx`, `DistillationTab.jsx`, plus the
  standalone `ShowcaseWorkspace.jsx` for the Pi demo.
  `components/AppErrorBoundary.jsx` wraps the whole app.
- `lib/api.js` is the single axios client, pointed at `/api/v1` with a `Authorization: Bearer
  ${REACT_APP_API_KEY}` default header; every backend endpoint the frontend calls goes through the
  `api` object here — check/update this file when adding or renaming backend routes.
  `api.searchShowcase(payload)` dispatches to `POST /search` or `POST /summary` based on
  `payload.mode` and reshapes the response back to the `{mode, ...}` shape
  `ShowcaseWorkspace.jsx` expects, so that component didn't need to change in Phase 1.
  `requirements`/`requirement` items may now carry `verdict`/`justification`/`facet` (Phase 2).
- `ShowcaseWorkspace.jsx`'s requirement-mode result cards show a `VerdictBadge` (Answers / Partial
  answer / Does not answer) and the justification/facet text per match when present, plus a notice
  when the response came back `unverified` (verification LLM unavailable).
- `components/ui/` holds generated shadcn/radix primitives (accordion, dialog, dropdown, etc.) —
  treat these as vendored, not hand-written app code.
- Path alias `@` → `frontend/src` is configured in `craco.config.js` (webpack alias) and
  `jsconfig.json`.
- `REACT_APP_BACKEND_URL` (frontend env) sets the API base URL; empty means same-origin (production
  mode where FastAPI serves the built frontend).

## Architecture — Node/Ollama prototype (frozen, `legacy/node-prototype/`)

`server.mjs` is a dependency-free Node `http` server: serves `public/` (vanilla JS/HTML/CSS SPA) and
handles `POST /api/review` by calling Ollama's `/api/chat` directly with `reviewer-system-prompt.md`
as the system prompt and `format: "json"`. Two helper modules in `src/`:

- `src/conflict-precheck.mjs` — heuristic pass (`findConflictCandidates`) that flags likely
  conflicting requirement pairs by regex-based positive/negative pattern matching before the LLM
  ever sees them; these are passed to the model as mandatory `conflictCandidates` to check, and
  `mergeCandidateConflicts` reconciles the LLM's output with them afterward. Ported to
  `backend/conflict_precheck.py`.
- `src/review-contract.mjs` — `validateReviewPayload` enforces/repairs the JSON schema the LLM is
  expected to return (per-requirement scores, conflicts list) against the actual input requirements.
  Not yet ported; `SPEC.md` calls for generalizing this into a Python "structured LLM I/O contract"
  module.

## Architecture — Streamlit prototype (frozen, `legacy/streamlit-prototype/`)

`req_analysis.py` implements deterministic (non-LLM) requirement parsing and scoring
(`parse_requirements`, `review_requirements`) — this is the baseline, ported to
`backend/deterministic_review.py`. `main.py` renders this in Streamlit and, if "Use Ollama
refinement" is checked, sends one batched Ollama chat call (system prompt chosen from `PROMPTS` —
`system_prompt_ZC.txt` for Zone Controller or `system_prompt_ADAS.txt` for ADAS camera context) to
refine scores/conflicts. `_merge_reviews`/`_merge_conflicts` guard against a malformed or incomplete
LLM response by falling back to the deterministic baseline whenever the returned requirement IDs
don't exactly match the input set — this specific reconciliation logic was not separately ported
(see `legacy/README.md` for why).

## Environment variables

Backend (`backend/.env`, see `backend/.env.example`): `OLLAMA_URL`, `OLLAMA_MODEL`,
`OLLAMA_EMBED_MODEL`, `REQUIREMENTS_DB_PATH`, `API_KEYS` (comma-separated, auth disabled if unset),
`ENABLE_LLM_SUMMARY`, `SUMMARY_TIMEOUT`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`,
`MONGO_URL`, `DB_NAME`, `CORS_ORIGINS`.

Frontend (see `frontend/.env.example`): `REACT_APP_BACKEND_URL`, `REACT_APP_API_KEY` (must match one
of the backend's `API_KEYS`).

Node prototype (`legacy/node-prototype/`): `PORT`, `OLLAMA_URL`, `OLLAMA_MODEL`.
