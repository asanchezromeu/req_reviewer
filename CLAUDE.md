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
python -m unittest backend.tests.test_showcase_unit -v          # stdlib-only, no server needed
python -m unittest backend.tests.test_conflict_precheck -v      # stdlib-only, no server needed
python -m unittest backend.tests.test_deterministic_review -v   # stdlib-only, no server needed
pytest backend/tests/test_reqiq_api.py                       # hits a running server (BASE_URL/REACT_APP_BACKEND_URL env, defaults to a hosted preview URL)
pytest backend/tests/test_reqiq_iter2.py
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

- Persistence is MongoDB via `motor`, but only if `MONGO_URL` is set. Otherwise `MemoryDatabase` /
  `MemoryCollection` (defined at the top of the file) provide an in-memory shim with the same async
  method signatures (`insert_one`, `find`, `update_one`, `delete_one`, ...), so the rest of the code
  is written once against a Mongo-like interface regardless of backend.
- `llm_complete(...)` is the single dispatch point for all LLM calls, branching on a `provider`
  string (`ollama`, `openai`, `anthropic`, `gemini`), each reading its own API key from env
  (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) / `OLLAMA_URL`. `AVAILABLE_MODELS` lists
  the selectable model IDs per provider surfaced to the frontend via `GET /api/models`.
- Two routers are mounted under `/api`: the main `api` router defined inline in `server.py`
  (uploads, analyze/individual, analyze/set, summarize/ask, classify/set, prompts library,
  training examples/datasets, distillation jobs), and `create_showcase_router(llm_complete)` from
  `backend/showcase.py`, mounted at `/api/showcase`.
- If `frontend/build` exists, it's mounted at `/` via `StaticFiles`, so a built frontend and the API
  are served from the same FastAPI process (the Raspberry Pi deployment model described in
  `README.md`). During development the frontend runs separately on :3000 instead.
- `backend/incose_rules.py` holds the INCOSE 8-rule definitions plus the system prompts used for
  individual requirement scoring, set-level consistency checks, and the plain-language summarizer.
- `backend/conflict_precheck.py` and `backend/deterministic_review.py` are standalone, tested
  utilities salvaged from the frozen prototypes (see `legacy/README.md`) — a heuristic conflict
  pre-check and a non-LLM deterministic scorer/parser, respectively. Neither is wired into any route
  yet; that wiring is future work.

**`backend/showcase.py`** is a self-contained module (its own SQLite store, embeddings, and search
ranking) for the "Raspberry Pi showcase" workspace described in `README.md`:

- `ShowcaseStore` persists requirements to SQLite (`backend/data/showcase.db` by default, override
  with `SHOWCASE_DB_PATH`).
- `IndexCoordinator` schedules a background embedding refresh (via Ollama's embeddings API,
  `OLLAMA_EMBED_MODEL`, default `embeddinggemma`) after every save/import, and exposes `status()` for
  polling from the frontend.
- Retrieval blends multiple signals — cosine similarity on embeddings plus `keyword_score`,
  `phrase_score`, `structural_score`, and `parameter_penalty` — in `ranked_matches`/`score_requirement`.
- Two search modes: **requirement mode** returns only the single closest match; **summary mode**
  (`broad_summary_sources` for broad queries, otherwise the top-N via `select_summary_sources`)
  retrieves several requirements and asks the local LLM (`ollama_showcase_summary`) for an executive
  summary, with `fallback_summary`/`summarize_quantitative_answer` as non-LLM fallbacks.

**Frontend** (`frontend/src/`):

- `App.js` is a single route (`/`) rendering `pages/Dashboard.jsx`, which composes the feature tabs:
  `components/AnalyzeTab.jsx`, `TailoringTab.jsx`, `TrainingTab.jsx`, `DistillationTab.jsx`, plus the
  standalone `ShowcaseWorkspace.jsx` for the Pi demo.
  `components/AppErrorBoundary.jsx` wraps the whole app.
- `lib/api.js` is the single axios client; every backend endpoint the frontend calls goes through
  the `api` object here — check/update this file when adding or renaming backend routes.
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
`OLLAMA_EMBED_MODEL`, `SHOWCASE_DB_PATH`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`,
`MONGO_URL`, `DB_NAME`, `CORS_ORIGINS`.

Frontend: `REACT_APP_BACKEND_URL`.

Node prototype (`legacy/node-prototype/`): `PORT`, `OLLAMA_URL`, `OLLAMA_MODEL`.
