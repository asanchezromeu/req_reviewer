import json
import unittest

try:
    from backend.model_registry import build_training_row, default_entry, score_matches_expectation
except ImportError:
    from model_registry import build_training_row, default_entry, score_matches_expectation


class ScoreMatchesExpectationTests(unittest.TestCase):
    def test_good_label_requires_high_score(self):
        self.assertTrue(score_matches_expectation("good", 90))
        self.assertTrue(score_matches_expectation("good", 85))
        self.assertFalse(score_matches_expectation("good", 84))

    def test_bad_label_requires_low_score(self):
        self.assertTrue(score_matches_expectation("bad", 84))
        self.assertFalse(score_matches_expectation("bad", 85))

    def test_unknown_label_does_not_penalize(self):
        self.assertTrue(score_matches_expectation("unknown", 10))


class DefaultEntryTests(unittest.TestCase):
    def test_default_entry_has_provider_and_model(self):
        entry = default_entry()
        self.assertEqual(entry["provider"], "ollama")
        self.assertTrue(entry["model"])


class BuildTrainingRowTests(unittest.TestCase):
    def test_good_example_row_shape(self):
        example = {
            "id": "abcdefgh-1111",
            "label": "good",
            "requirement_text": "The zone controller shall respond to a diagnostic request within 200 ms under nominal load.",
            "explanation": "",
            "corrected_text": "",
        }
        row = build_training_row(example, "SYSTEM PROMPT")
        self.assertEqual(row["messages"][0], {"role": "system", "content": "SYSTEM PROMPT"})
        self.assertIn("EX-abcdefgh", row["messages"][1]["content"])
        assistant = json.loads(row["messages"][2]["content"])
        self.assertGreaterEqual(assistant["overall_score"], 85)
        self.assertEqual(assistant["proposed_fix"], "")

    def test_bad_example_with_correction_row_shape(self):
        example = {
            "id": "zzzzzzzz-2222",
            "label": "bad",
            "requirement_text": "The system shall be fast.",
            "explanation": "Vague, not measurable.",
            "corrected_text": "The system shall respond within 200 ms under nominal load.",
        }
        row = build_training_row(example, "SYSTEM PROMPT")
        assistant = json.loads(row["messages"][2]["content"])
        self.assertEqual(assistant["summary"], "Vague, not measurable.")
        self.assertEqual(assistant["proposed_fix"], "The system shall respond within 200 ms under nominal load.")


if __name__ == "__main__":
    unittest.main()
