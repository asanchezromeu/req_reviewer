# Legacy prototypes

Both directories here are **frozen**: no new feature work goes into them. They exist as reference
implementations from before the project converged on `backend/` + `frontend/` as the one engine
(see `../SPEC.md`, "One engine"). They're kept for their tests and because their heuristics were
useful enough to salvage — see below for exactly what moved where.

## `node-prototype/`

The original dependency-free Node/Ollama demo: `server.mjs` (plain `http` server), `public/` (vanilla
JS/HTML/CSS SPA), and `src/` (`conflict-precheck.mjs`, `parse-requirements.mjs`,
`review-contract.mjs`). Run with `npm start` (serves on `:3000`, needs a running Ollama). Tests:
`npm test` finds 0 tests via bare `node --test` in this Node version (a pre-existing discovery
quirk, not a regression from the move) — run explicitly instead:
`node --test test/parse-requirements.test.mjs`.

## `streamlit-prototype/`

The original Streamlit demo: `main.py` (UI), `req_analysis.py` (deterministic scoring/parsing),
`system_prompt_ZC.txt` / `system_prompt_ADAS.txt` (domain system prompts for the optional Ollama
refinement pass), `samples/` and `templates/` (sample/template CSV+JSON, used by `main.py` and by
`tests/test_req_analysis.py`). Run with `streamlit run main.py`. Tests:
`python -m unittest tests.test_req_analysis -v` (run from this directory).

## What was salvaged into `backend/`

- **`backend/conflict_precheck.py`** — a faithful Python port of
  `node-prototype/src/conflict-precheck.mjs`'s heuristic conflict pre-pass (`find_conflict_candidates`,
  `merge_candidate_conflicts`): keyword-Jaccard similarity + obligation-polarity regexes to flag
  likely contradictions and threshold mismatches before an LLM call, and to reconcile an LLM review's
  conflict list against those candidates afterward. Covered by
  `backend/tests/test_conflict_precheck.py`. Not wired into any route yet — that's Phase 1/2 work per
  `SPEC.md`.
- **`backend/deterministic_review.py`** — a port of `streamlit-prototype/req_analysis.py`'s
  non-LLM parsing and scoring (`parse_requirements`, `review_requirement(s)`, `detect_conflicts`).
  Covered by `backend/tests/test_deterministic_review.py`. Intended as the non-LLM fallback and
  regression oracle called out in `SPEC.md` section 6 — not wired into any route yet.

`streamlit-prototype/main.py`'s `_merge_reviews`/`_merge_conflicts` (reconciling a possibly-incomplete
LLM response against a deterministic baseline) was **not** separately ported: the same id-anchored
reconciliation pattern is already covered, more generally, by `review-contract.mjs`'s
`validateReviewPayload` idea, which `SPEC.md` calls out for its own Python port (a
"structured LLM I/O contract module", Phase 2) — porting `_merge_reviews` on top would have been
redundant with that upcoming work.
