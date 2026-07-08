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
  backend.tests.test_review_fallback backend.tests.test_search_verification -v
  # ^ all stdlib+fastapi TestClient only, no live server or Ollama needed (LLM calls are mocked)
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
  `select_summary_sources`) retrieves several requirements and asks the local LLM (`ollama_summary`)
  for an executive summary, with `fallback_summary`/`summarize_quantitative_answer` as non-LLM
  fallbacks (`degraded: true` in the response when the LLM path wasn't used).
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
