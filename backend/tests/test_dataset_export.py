import asyncio
import json
import unittest

from fastapi import HTTPException

try:
    from backend import server
except ImportError:
    import server


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class DatasetExportTests(unittest.TestCase):
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

    def test_empty_corpus_returns_400(self):
        with self.assertRaises(HTTPException) as ctx:
            run(server.export_dataset(label=None))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_exports_one_jsonl_line_per_example(self):
        self._add_example("good", "The system shall respond within 200 ms.")
        self._add_example("bad", "The system shall be fast.", "Vague.", "The system shall respond within 200 ms.")

        response = run(server.export_dataset(label=None))

        self.assertEqual(response.media_type, "application/jsonl")
        lines = response.body.decode("utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)
        for line in lines:
            row = json.loads(line)
            self.assertEqual(len(row["messages"]), 3)
            json.loads(row["messages"][2]["content"])  # assistant content is itself valid JSON

    def test_label_filter(self):
        self._add_example("good", "The system shall respond within 200 ms.")
        self._add_example("bad", "The system shall be fast.")

        response = run(server.export_dataset(label="good"))

        lines = response.body.decode("utf-8").strip().split("\n")
        self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()
