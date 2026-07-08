import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from backend import retrieval
    from backend.retrieval import create_requirements_router, summary_violates_contract
except ImportError:
    import retrieval
    from retrieval import create_requirements_router, summary_violates_contract

from fastapi import FastAPI
from fastapi.testclient import TestClient


SOURCES = [
    {
        "id": "REQ-001",
        "text": "The zone controller shall respond to a diagnostic request within 200 ms under nominal load.",
        "source": "SN-002",
        "distance": 0.1,
        "similarity": 0.9,
        "score": 0.9,
        "breakdown": {},
    },
    {
        "id": "REQ-002",
        "text": "The zone controller shall log all authentication failures to the audit trail.",
        "source": "SN-003",
        "distance": 0.2,
        "similarity": 0.8,
        "score": 0.8,
        "breakdown": {},
    },
]


class SummaryContractUnitTests(unittest.TestCase):
    def test_quantitative_restatement_is_not_flagged(self):
        answer = (
            "The zone controller responds quickly to diagnostics, doing so in 200 ms under normal "
            "conditions, and it also logs failed logins for auditing.\nSources: REQ-001, REQ-002"
        )
        self.assertIsNone(summary_violates_contract(answer, SOURCES))

    def test_full_verbatim_sentence_is_flagged(self):
        answer = (
            "In short: The zone controller shall respond to a diagnostic request within 200 ms "
            "under nominal load. Sources: REQ-001"
        )
        violation = summary_violates_contract(answer, SOURCES)
        self.assertIsNotNone(violation)
        self.assertIn("REQ-001", violation)


class SummaryRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self._original_enable = os.environ.get("ENABLE_LLM_SUMMARY")

        async def default_llm_complete(*args, **kwargs):
            raise AssertionError("llm_complete should not be called by /summary directly")

        self.router = create_requirements_router(
            default_llm_complete, database_path=Path(self.temp_dir.name) / "requirements.db"
        )
        app = FastAPI()
        app.include_router(self.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.temp_dir.cleanup()
        if self._original_enable is None:
            os.environ.pop("ENABLE_LLM_SUMMARY", None)
        else:
            os.environ["ENABLE_LLM_SUMMARY"] = self._original_enable

    def _fixed_ranking(self):
        return {
            "ranked": SOURCES,
            "matches": SOURCES,
            "discarded": 0,
            "threshold": 0.0,
            "best_similarity": 0.9,
            "best_score": 0.9,
        }

    def _summarize(self, fake_ollama_summary):
        os.environ["ENABLE_LLM_SUMMARY"] = "true"

        async def fake_embed_query(*args, **kwargs):
            return "How fast must diagnostics respond?", [], [1.0, 0.0], self._fixed_ranking()

        with patch.object(retrieval, "_embed_query", fake_embed_query), patch.object(
            retrieval, "ollama_summary", fake_ollama_summary
        ):
            return self.client.post(
                "/summary",
                json={"query": "How fast must diagnostics respond?"},
            )

    def test_compliant_answer_is_used_as_is(self):
        def fake_ollama_summary(url, model, question, sources, timeout, fewshot_prefix="", reference_context=""):
            return (
                "Diagnostics must complete within 200 ms under normal load, and authentication "
                "failures are logged for audit purposes.\nSources: REQ-001, REQ-002"
            )

        response = self._summarize(fake_ollama_summary)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["degraded"])
        self.assertIsNone(body["degraded_reason"])
        self.assertIn("200 ms", body["summary_text"])
        self.assertEqual(set(body["source_ids"]), {"REQ-001", "REQ-002"})

    def test_verbatim_answer_degrades_to_fallback(self):
        def fake_ollama_summary(url, model, question, sources, timeout, fewshot_prefix="", reference_context=""):
            return "The zone controller shall respond to a diagnostic request within 200 ms under nominal load."

        response = self._summarize(fake_ollama_summary)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["degraded"])
        self.assertIn("verbatim quote", body["degraded_reason"])
        self.assertEqual(set(body["source_ids"]), {"REQ-001", "REQ-002"})

    def test_ollama_down_degrades_gracefully(self):
        def fake_ollama_summary(url, model, question, sources, timeout, fewshot_prefix="", reference_context=""):
            raise ConnectionError("Ollama is not reachable")

        response = self._summarize(fake_ollama_summary)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["degraded"])
        self.assertIn("not reachable", body["degraded_reason"])
        self.assertTrue(len(body["summary_text"]) > 0)
        self.assertEqual(set(body["source_ids"]), {"REQ-001", "REQ-002"})


if __name__ == "__main__":
    unittest.main()
