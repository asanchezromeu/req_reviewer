import tempfile
import unittest
from pathlib import Path

try:
    from backend import supporting_info
except ImportError:
    import supporting_info


class SupportingInfoTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "supporting_info.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_missing_file_returns_empty_list(self):
        self.assertEqual(supporting_info.load_supporting_info(self.path), [])

    def test_append_then_load_round_trips(self):
        fact = supporting_info.append_supporting_fact(
            "All electrical faults are simulated with electronic loads or fault injection.",
            source="seed",
            path=self.path,
        )
        self.assertEqual(fact["source"], "seed")
        self.assertIsNone(fact["requirement_id"])
        self.assertTrue(fact["id"])
        self.assertTrue(fact["added_at"])

        loaded = supporting_info.load_supporting_info(self.path)
        self.assertEqual(loaded, [fact])

    def test_append_twice_accumulates(self):
        supporting_info.append_supporting_fact("Fact one.", source="manual", path=self.path)
        supporting_info.append_supporting_fact("Fact two.", source="manual", path=self.path)
        loaded = supporting_info.load_supporting_info(self.path)
        self.assertEqual([f["text"] for f in loaded], ["Fact one.", "Fact two."])

    def test_remove_existing_fact_returns_true_and_removes_it(self):
        fact = supporting_info.append_supporting_fact("Fact one.", source="manual", path=self.path)
        supporting_info.append_supporting_fact("Fact two.", source="manual", path=self.path)

        removed = supporting_info.remove_supporting_fact(fact["id"], path=self.path)
        self.assertTrue(removed)
        loaded = supporting_info.load_supporting_info(self.path)
        self.assertEqual([f["text"] for f in loaded], ["Fact two."])

    def test_remove_unknown_fact_returns_false(self):
        self.assertFalse(supporting_info.remove_supporting_fact("nope", path=self.path))

    def test_format_supporting_info_empty(self):
        self.assertEqual(supporting_info.format_supporting_info([]), "(no supporting info recorded yet)")

    def test_format_supporting_info_lists_each_fact_text(self):
        facts = [{"text": "Fact one."}, {"text": "Fact two."}]
        formatted = supporting_info.format_supporting_info(facts)
        self.assertEqual(formatted, "- Fact one.\n- Fact two.")


if __name__ == "__main__":
    unittest.main()
