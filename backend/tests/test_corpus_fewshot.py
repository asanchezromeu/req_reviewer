import asyncio
import unittest
from unittest.mock import patch

try:
    from backend import server
except ImportError:
    import server


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FewshotPrefixTests(unittest.TestCase):
    def setUp(self):
        self._inserted_ids = []

    def tearDown(self):
        for ex_id in self._inserted_ids:
            run(server.db.training_examples.delete_one({"id": ex_id}))

    def _add_example(self, label, requirement_text, explanation="", corrected_text=""):
        example = server.TrainingExample(
            label=label,
            requirement_text=requirement_text,
            explanation=explanation,
            corrected_text=corrected_text,
        )
        run(server.db.training_examples.insert_one(example.model_dump()))
        self._inserted_ids.append(example.id)
        return example

    def test_empty_corpus_returns_empty_string(self):
        self.assertEqual(run(server.get_fewshot_prefix()), "")

    def test_formats_good_and_bad_examples_and_caps_per_label(self):
        self._add_example("good", "The system shall respond within 200 ms.", "Measurable, verifiable.")
        self._add_example("bad", "The system shall be fast.", "Vague.", "The system shall respond within 200 ms.")
        for i in range(5):
            self._add_example("good", f"Extra good example {i}.")

        prefix = run(server.get_fewshot_prefix(max_per_label=3))

        self.assertIn("CURATED EXAMPLES", prefix)
        self.assertIn("GOOD:", prefix)
        self.assertIn("BAD:", prefix)
        self.assertIn("The system shall be fast.", prefix)
        self.assertIn("fix:", prefix)
        # Capped at 3 good examples even though 6 exist.
        self.assertEqual(prefix.count("GOOD:"), 3)
        self.assertEqual(prefix.count("BAD:"), 1)

    def test_review_requirement_prompt_includes_fewshot_block_when_corpus_populated(self):
        self._add_example("good", "The system shall respond within 200 ms.", "Measurable.")
        captured = {}

        async def fake_llm_complete(provider, model, sys_msg, user, ollama_url=None):
            captured["sys_msg"] = sys_msg
            return '{"overall_score": 90, "summary": "ok", "proposed_fix": "", "rules": {}}'

        with patch.object(server, "llm_complete", fake_llm_complete):
            body = server.ReviewRequirementBody(
                text="The zone controller shall log all failures.",
                provider="ollama",
                model="gemma3:1b",
            )
            run(server.review_requirement_endpoint(body))

        self.assertIn("CURATED EXAMPLES", captured["sys_msg"])
        self.assertIn("200 ms", captured["sys_msg"])

    def test_review_requirement_prompt_unaffected_when_corpus_empty(self):
        captured = {}

        async def fake_llm_complete(provider, model, sys_msg, user, ollama_url=None):
            captured["sys_msg"] = sys_msg
            return '{"overall_score": 90, "summary": "ok", "proposed_fix": "", "rules": {}}'

        with patch.object(server, "llm_complete", fake_llm_complete):
            body = server.ReviewRequirementBody(
                text="The zone controller shall log all failures.",
                provider="ollama",
                model="gemma3:1b",
            )
            run(server.review_requirement_endpoint(body))

        self.assertNotIn("CURATED EXAMPLES", captured["sys_msg"])


if __name__ == "__main__":
    unittest.main()
