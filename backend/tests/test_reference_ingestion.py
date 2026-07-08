import json
import tempfile
import threading
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


def fake_embed(url, model, texts):
    return [[1.0, 0.0] for _ in texts]


class ReferenceIngestionRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

        async def default_llm_complete(*args, **kwargs):
            raise AssertionError("llm_complete should be monkeypatched per-test")

        self.router = create_requirements_router(
            default_llm_complete, database_path=Path(self.temp_dir.name) / "requirements.db"
        )
        # IndexCoordinator's `embedder` default is bound at class-definition time
        # (Python's eager default-argument evaluation), so patching the module-level
        # `ollama_embed` name never reaches an already-constructed instance - set it
        # directly instead, on both coordinators this router owns.
        self.router.indexer.embedder = fake_embed
        self.router.reference_indexer.embedder = fake_embed

        app = FastAPI()
        app.include_router(self.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _wait_for_reference_index_ready(self, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.client.get("/corpus/references/index/status").json()
            if status["state"] == "ready":
                return status
            time.sleep(0.02)
        self.fail("reference index never became ready")

    def test_ingest_and_list_references(self):
        response = self.client.post(
            "/corpus/references",
            json={
                "document": "ISO-16750",
                "title": "Environmental conditions",
                "text": "Electrical loads shall be tested per class B.\n\nHumidity shall be controlled during test.",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["chunks_added"], 2)

        self._wait_for_reference_index_ready()

        listing = self.client.get("/corpus/references").json()
        self.assertEqual(len(listing["chunks"]), 2)
        self.assertTrue(all(chunk["document"] == "ISO-16750" for chunk in listing["chunks"]))

    def test_ingest_is_additive_across_calls(self):
        self.client.post(
            "/corpus/references",
            json={"document": "GLOSSARY", "text": "Nominal means the expected typical value."},
        )
        self.client.post(
            "/corpus/references",
            json={"document": "GLOSSARY", "text": "Degraded mode is a reduced-capability operating state."},
        )
        listing = self.client.get("/corpus/references").json()
        self.assertEqual(len(listing["chunks"]), 2)

    def test_empty_ingest_rejected(self):
        response = self.client.post("/corpus/references", json={"document": "EMPTY", "text": "   "})
        self.assertEqual(response.status_code, 400)

    def test_concurrent_ingest_of_same_document_does_not_lose_chunks(self):
        # Regression test for a TOCTOU race: two concurrent ingests of the
        # same document used to be able to read the same existing_count and
        # collide on the same chunk_id, silently overwriting one chunk.
        store = self.router.store
        barrier = threading.Barrier(2)
        errors = []

        def ingest(text):
            try:
                barrier.wait(timeout=5)
                store.add_reference_chunks("RACE-DOC", None, [text])
            except Exception as exc:  # pragma: no cover - surfaced via `errors`
                errors.append(exc)

        threads = [
            threading.Thread(target=ingest, args=("Chunk A content.",)),
            threading.Thread(target=ingest, args=("Chunk B content.",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        chunks = store.list_reference_chunks()
        self.assertEqual(len(chunks), 2, f"expected both concurrent chunks to survive, got: {chunks}")
        texts = {chunk["text"] for chunk in chunks}
        self.assertEqual(texts, {"Chunk A content.", "Chunk B content."})

    def test_search_injects_reference_context_into_verification_prompt(self):
        rows = [Requirement(id="REQ-001", text="The zone controller shall log all authentication failures.")]
        self.router.store.replace_requirements(rows)

        self.client.post(
            "/corpus/references",
            json={"document": "GLOSSARY", "text": "Authentication failure means a rejected login attempt."},
        )
        self._wait_for_reference_index_ready()

        fixed_matches = [
            {
                "id": "REQ-001",
                "text": "The zone controller shall log all authentication failures.",
                "source": None,
                "distance": 0.0,
                "similarity": 1.0,
                "score": 1.0,
                "breakdown": {},
            }
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
            return "How are auth failures handled?", [], [1.0, 0.0], fixed_ranking

        captured = {}

        async def capturing_verify_candidates(llm_complete, provider, model, ollama_url, query, candidates, fewshot_prefix="", reference_context=""):
            captured["reference_context"] = reference_context
            return (
                [{"id": c["id"], "verdict": "does_not_answer", "justification": "n/a", "facet": None} for c in candidates],
                False,
            )

        with patch.object(retrieval, "_embed_query", fake_embed_query), patch.object(
            retrieval, "verify_candidates", capturing_verify_candidates
        ):
            response = self.client.post("/search", json={"query": "How are auth failures handled?"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("rejected login attempt", captured["reference_context"])

    def test_summary_injects_reference_context(self):
        self.client.post(
            "/corpus/references",
            json={"document": "GLOSSARY", "text": "Authentication failure means a rejected login attempt."},
        )
        self._wait_for_reference_index_ready()

        sources = [
            {
                "id": "REQ-001",
                "text": "The zone controller shall log all authentication failures.",
                "source": None,
                "distance": 0.0,
                "similarity": 1.0,
                "score": 1.0,
                "breakdown": {},
            }
        ]
        fixed_ranking = {
            "ranked": sources,
            "matches": sources,
            "discarded": 0,
            "threshold": 0.0,
            "best_similarity": 1.0,
            "best_score": 1.0,
        }

        async def fake_embed_query(*args, **kwargs):
            return "How are auth failures handled?", sources, [1.0, 0.0], fixed_ranking

        captured = {}

        def fake_ollama_summary(url, model, question, srcs, timeout, fewshot_prefix="", reference_context=""):
            captured["reference_context"] = reference_context
            return "Failures are logged.\nSources: REQ-001"

        with patch.object(retrieval, "_embed_query", fake_embed_query), patch.object(
            retrieval, "ollama_summary", fake_ollama_summary
        ), patch.dict("os.environ", {"ENABLE_LLM_SUMMARY": "true"}):
            response = self.client.post("/summary", json={"query": "How are auth failures handled?"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("rejected login attempt", captured["reference_context"])


if __name__ == "__main__":
    unittest.main()
