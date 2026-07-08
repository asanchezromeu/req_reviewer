import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

try:
    from backend import server
except ImportError:
    import server


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


GOOD_TEXT = "The zone controller shall respond to a diagnostic request within 200 ms under nominal load."
BAD_TEXT = "The system shall be fast."


class ModelEvaluateTests(unittest.TestCase):
    def setUp(self):
        self._inserted_ids = []

    def tearDown(self):
        for ex_id in self._inserted_ids:
            run(server.db.training_examples.delete_one({"id": ex_id}))
        run(server.db.model_registry.delete_one({"id": "review"}))

    def _add_example(self, label, requirement_text):
        example = server.TrainingExample(label=label, requirement_text=requirement_text)
        run(server.db.training_examples.insert_one(example.model_dump()))
        self._inserted_ids.append(example.id)
        return example

    def test_unsupported_feature_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            run(
                server.evaluate_model(
                    server.EvaluateBody(feature="search", candidate_provider="ollama", candidate_model="x")
                )
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_no_examples_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            run(
                server.evaluate_model(
                    server.EvaluateBody(candidate_provider="ollama", candidate_model="x")
                )
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_explicit_empty_example_ids_is_rejected_not_treated_as_whole_corpus(self):
        # Regression test: an explicit `example_ids: []` must not silently
        # fall back to evaluating the entire corpus.
        self._add_example("good", GOOD_TEXT)

        with self.assertRaises(HTTPException) as ctx:
            run(
                server.evaluate_model(
                    server.EvaluateBody(candidate_provider="ollama", candidate_model="x", example_ids=[])
                )
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_ollama_url_is_passed_through_to_both_models(self):
        # Regression test: EvaluateBody.ollama_url must actually reach analyze_one.
        self._add_example("good", GOOD_TEXT)
        captured_urls = []

        async def fake_llm_complete(provider, model, sys_msg, user, ollama_url=None):
            captured_urls.append(ollama_url)
            return json.dumps({"overall_score": 90, "summary": "x", "proposed_fix": "", "rules": {}})

        with patch.object(server, "llm_complete", fake_llm_complete):
            run(
                server.evaluate_model(
                    server.EvaluateBody(
                        candidate_provider="ollama",
                        candidate_model="model-b",
                        ollama_url="http://gpu-box:11434",
                    )
                )
            )

        self.assertTrue(captured_urls)
        self.assertTrue(all(url == "http://gpu-box:11434" for url in captured_urls))

    def test_candidate_outperforms_baseline(self):
        self._add_example("good", GOOD_TEXT)
        self._add_example("bad", BAD_TEXT)

        async def fake_llm_complete(provider, model, sys_msg, user, ollama_url=None):
            # baseline ("model-a") always gets it wrong; candidate ("model-b") always right.
            if model == "model-a":
                score = 40 if "200 ms" in user else 90
            else:
                score = 90 if "200 ms" in user else 40
            return json.dumps({"overall_score": score, "summary": "x", "proposed_fix": "", "rules": {}})

        run(server.set_model_registry_entry("review", server.RegistryUpdateBody(provider="ollama", model="model-a")))

        with patch.object(server, "llm_complete", fake_llm_complete):
            result = run(
                server.evaluate_model(
                    server.EvaluateBody(candidate_provider="ollama", candidate_model="model-b")
                )
            )

        self.assertEqual(result["baseline"]["accuracy"], 0.0)
        self.assertEqual(result["candidate"]["accuracy"], 1.0)
        self.assertEqual(len(result["detail"]), 2)

    def test_malformed_llm_output_counts_as_degraded_on_both_sides(self):
        self._add_example("good", GOOD_TEXT)

        async def fake_llm_complete(provider, model, sys_msg, user, ollama_url=None):
            return "not json"

        run(server.set_model_registry_entry("review", server.RegistryUpdateBody(provider="ollama", model="model-a")))

        with patch.object(server, "llm_complete", fake_llm_complete):
            result = run(
                server.evaluate_model(
                    server.EvaluateBody(candidate_provider="ollama", candidate_model="model-b")
                )
            )

        self.assertEqual(result["baseline"]["degraded_count"], 1)
        self.assertEqual(result["candidate"]["degraded_count"], 1)


if __name__ == "__main__":
    unittest.main()
