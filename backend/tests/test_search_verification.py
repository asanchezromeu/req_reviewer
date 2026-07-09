import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from backend import retrieval
    from backend.retrieval import Requirement, create_requirements_router
except ImportError:
    import retrieval
    from retrieval import Requirement, create_requirements_router

from fastapi import FastAPI
from fastapi.testclient import TestClient


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


CANDIDATES = [
    {"id": "REQ-001", "text": "In operation mode, the system shall withstand a nominal current of 1A."},
    {"id": "REQ-002", "text": "In degraded mode, the system shall withstand a nominal current of 20A."},
    {"id": "REQ-003", "text": "The system shall log all authentication failures."},
]


def _llm_returning(payload):
    async def fake(*args, **kwargs):
        return json.dumps(payload)

    return fake


class VerifyCandidatesTests(unittest.TestCase):
    def test_single_clear_answer(self):
        fake_llm = _llm_returning(
            {
                "verdicts": [
                    {"id": "REQ-001", "verdict": "answers", "justification": "Directly states the current.", "facet": ""},
                    {"id": "REQ-002", "verdict": "does_not_answer", "justification": "Different mode.", "facet": ""},
                    {"id": "REQ-003", "verdict": "does_not_answer", "justification": "Unrelated.", "facet": ""},
                ]
            }
        )

        verdicts, unverified = run(
            retrieval.verify_candidates(fake_llm, "ollama", "gemma3:1b", "http://x", "nominal current?", CANDIDATES)
        )

        self.assertFalse(unverified)
        self.assertEqual([v["id"] for v in verdicts], ["REQ-001", "REQ-002", "REQ-003"])
        self.assertEqual(verdicts[0]["verdict"], "answers")

    def test_multiple_facets(self):
        fake_llm = _llm_returning(
            {
                "verdicts": [
                    {"id": "REQ-001", "verdict": "partially_answers", "justification": "Operation mode value.", "facet": "operation mode"},
                    {"id": "REQ-002", "verdict": "partially_answers", "justification": "Degraded mode value.", "facet": "degraded mode"},
                    {"id": "REQ-003", "verdict": "does_not_answer", "justification": "Unrelated.", "facet": ""},
                ]
            }
        )

        verdicts, unverified = run(
            retrieval.verify_candidates(fake_llm, "ollama", "gemma3:1b", "http://x", "nominal current?", CANDIDATES)
        )

        self.assertFalse(unverified)
        facets = {v["id"]: v["facet"] for v in verdicts if v["verdict"] == "partially_answers"}
        self.assertEqual(facets, {"REQ-001": "operation mode", "REQ-002": "degraded mode"})

    def test_no_answer_case(self):
        fake_llm = _llm_returning(
            {
                "verdicts": [
                    {"id": rid, "verdict": "does_not_answer", "justification": "Unrelated.", "facet": ""}
                    for rid in ("REQ-001", "REQ-002", "REQ-003")
                ]
            }
        )

        verdicts, unverified = run(
            retrieval.verify_candidates(fake_llm, "ollama", "gemma3:1b", "http://x", "what is the tire pressure?", CANDIDATES)
        )

        self.assertFalse(unverified)
        self.assertTrue(all(v["verdict"] == "does_not_answer" for v in verdicts))

    def test_malformed_json_falls_back_per_candidate_without_crashing(self):
        async def fake_llm(*args, **kwargs):
            return "this is not json"

        verdicts, unverified = run(
            retrieval.verify_candidates(fake_llm, "ollama", "gemma3:1b", "http://x", "nominal current?", CANDIDATES)
        )

        self.assertFalse(unverified)
        self.assertTrue(all(v["verdict"] == "does_not_answer" for v in verdicts))
        self.assertTrue(all("unavailable" in v["justification"].lower() for v in verdicts))

    def test_llm_failure_marks_unverified_without_crashing(self):
        async def fake_llm(*args, **kwargs):
            raise RuntimeError("connection refused")

        verdicts, unverified = run(
            retrieval.verify_candidates(fake_llm, "ollama", "gemma3:1b", "http://x", "nominal current?", CANDIDATES)
        )

        self.assertTrue(unverified)
        self.assertEqual(verdicts, [])

    def test_missing_candidate_in_llm_response_gets_fallback(self):
        fake_llm = _llm_returning(
            {"verdicts": [{"id": "REQ-001", "verdict": "answers", "justification": "ok", "facet": ""}]}
        )

        verdicts, unverified = run(
            retrieval.verify_candidates(fake_llm, "ollama", "gemma3:1b", "http://x", "nominal current?", CANDIDATES)
        )

        self.assertFalse(unverified)
        by_id = {v["id"]: v for v in verdicts}
        self.assertEqual(by_id["REQ-002"]["verdict"], "does_not_answer")
        self.assertEqual(by_id["REQ-003"]["verdict"], "does_not_answer")


class SearchCardinalityRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

        async def default_llm_complete(*args, **kwargs):
            raise AssertionError("llm_complete should be monkeypatched per-test")

        self.router = create_requirements_router(
            default_llm_complete, database_path=Path(self.temp_dir.name) / "requirements.db"
        )
        app = FastAPI()
        app.include_router(self.router)
        self.client = TestClient(app)

        rows = [Requirement(id=item["id"], text=item["text"]) for item in CANDIDATES]
        self.router.store.replace_requirements(rows)

    def tearDown(self):
        # See test_reference_ingestion.py's tearDown for why this wait exists -
        # a still-running background IndexCoordinator thread can hold the
        # SQLite file open past the temp dir cleanup on Windows.
        deadline = time.time() + 5
        while time.time() < deadline:
            if not self.router.indexer.running and not self.router.reference_indexer.running:
                break
            time.sleep(0.02)
        self.temp_dir.cleanup()

    def _search(self, verdict_payload):
        # Bypass real embedding/keyword ranking entirely - this test is about
        # cardinality logic given verdicts, not about ranking/discarding quality
        # (that's covered in test_retrieval.py). Return all 3 candidates as
        # equally-ranked "matches" so cardinality is driven purely by the
        # (mocked) verification verdicts below.
        fixed_matches = [
            {
                "id": item["id"],
                "text": item["text"],
                "source": None,
                "distance": 0.0,
                "similarity": 1.0,
                "score": 1.0,
                "breakdown": {},
            }
            for item in CANDIDATES
        ]
        fixed_ranking = {
            "ranked": fixed_matches,
            "matches": fixed_matches,
            "discarded": 0,
            "threshold": 0.0,
            "best_similarity": 1.0,
            "best_score": 1.0,
        }

        async def fake_embed_query(*args, **kwargs):
            return "nominal current?", [], [1.0, 0.0], fixed_ranking

        async def fake_verify(*args, **kwargs):
            return verdict_payload, False

        with patch.object(retrieval, "_embed_query", fake_embed_query), patch.object(
            retrieval, "verify_candidates", fake_verify
        ):
            return self.client.post(
                "/search",
                json={"query": "nominal current?", "min_similarity": 0.0},
            )

    def test_single_answer_cardinality(self):
        response = self._search(
            [
                {"id": "REQ-001", "verdict": "answers", "justification": "ok", "facet": None},
                {"id": "REQ-002", "verdict": "does_not_answer", "justification": "no", "facet": None},
                {"id": "REQ-003", "verdict": "does_not_answer", "justification": "no", "facet": None},
            ]
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["answered"])
        self.assertIsNotNone(body["requirement"])
        self.assertEqual(body["requirement"]["id"], "REQ-001")

    def test_multiple_facets_cardinality(self):
        response = self._search(
            [
                {"id": "REQ-001", "verdict": "partially_answers", "justification": "ok", "facet": "operation mode"},
                {"id": "REQ-002", "verdict": "partially_answers", "justification": "ok", "facet": "degraded mode"},
                {"id": "REQ-003", "verdict": "does_not_answer", "justification": "no", "facet": None},
            ]
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["answered"])
        self.assertIsNone(body["requirement"])
        answering = [r for r in body["requirements"] if r["verdict"] == "partially_answers"]
        self.assertEqual(len(answering), 2)
        self.assertEqual({r["facet"] for r in answering}, {"operation mode", "degraded mode"})

    def test_no_answer_cardinality(self):
        response = self._search(
            [
                {"id": rid, "verdict": "does_not_answer", "justification": "no", "facet": None}
                for rid in ("REQ-001", "REQ-002", "REQ-003")
            ]
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["answered"])
        self.assertIsNone(body["requirement"])
        self.assertEqual(len(body["requirements"]), 3)
        self.assertIn("no requirement", body["message"].lower())


if __name__ == "__main__":
    unittest.main()
