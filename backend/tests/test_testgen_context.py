import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

try:
    from backend import server
    from backend.retrieval import Requirement, RequirementStore
except ImportError:
    import server
    from retrieval import Requirement, RequirementStore


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestgenContextTests(unittest.TestCase):
    def setUp(self):
        # server.requirements_store is the real, persistent-file-backed global
        # store used by the live app - never touch it directly. Patch in an
        # isolated, temp-file-backed store for the duration of each test.
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = RequirementStore(Path(self.temp_dir.name) / "requirements.db")
        self.store_patcher = patch.object(server, "requirements_store", self.store)
        self.store_patcher.start()

    def tearDown(self):
        self.store_patcher.stop()
        self.temp_dir.cleanup()
        while True:
            doc = run(server.get_current_test_context())
            if not doc:
                break
            run(server.db.test_context_versions.delete_one({"id": doc["id"]}))

    def _seed_requirement(self, req_id, text):
        self.store.replace_requirements([Requirement(id=req_id, text=text)])

    def test_analyze_requires_requirements(self):
        with self.assertRaises(HTTPException) as ctx:
            run(server.analyze_test_context(server.ContextAnalyzeBody()))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_analyze_creates_version_1_with_items_and_questions(self):
        self._seed_requirement("REQ-001", "The zone controller shall withstand 12 V +/- 10% nominal supply.")

        async def fake_llm_complete(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps(
                {
                    "items": [
                        {
                            "category": "parameter",
                            "key": "nominal supply voltage",
                            "value": "12 V +/- 10%",
                            "source_requirement_ids": ["REQ-001"],
                        }
                    ],
                    "questions": [
                        {"question": "What test rig is available?", "reason": "Needed to define preconditions."}
                    ],
                }
            )

        with patch.object(server, "llm_complete", fake_llm_complete):
            context = run(server.analyze_test_context(server.ContextAnalyzeBody()))

        self.assertEqual(context.version, 1)
        self.assertEqual(len(context.items), 1)
        self.assertEqual(context.items[0].source_requirement_ids, ["REQ-001"])
        self.assertEqual(len(context.questions), 1)
        self.assertEqual(context.questions[0].status, "open")

    def test_analyze_malformed_llm_output_degrades_gracefully(self):
        self._seed_requirement("REQ-001", "The zone controller shall log all authentication failures.")

        async def fake_llm_complete(provider, model, sys_msg, user, ollama_url=None):
            return "not json"

        with patch.object(server, "llm_complete", fake_llm_complete):
            context = run(server.analyze_test_context(server.ContextAnalyzeBody()))

        self.assertEqual(context.items, [])
        self.assertEqual(len(context.questions), 1)
        self.assertIn("failed", context.questions[0].question.lower())

    def test_get_context_404_before_first_analyze(self):
        with self.assertRaises(HTTPException) as ctx:
            run(server.get_test_context())
        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_questions_returns_empty_list_before_first_analyze(self):
        self.assertEqual(run(server.get_test_context_questions()), [])

    def test_patch_requires_existing_context(self):
        with self.assertRaises(HTTPException) as ctx:
            run(server.patch_test_context(server.ContextPatchBody()))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_patch_updates_item_and_bumps_version(self):
        self._seed_requirement("REQ-001", "The zone controller shall withstand 12 V nominal supply.")

        async def fake_llm_complete(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps(
                {
                    "items": [
                        {"category": "parameter", "key": "voltage", "value": "12 V", "source_requirement_ids": ["REQ-001"]}
                    ],
                    "questions": [],
                }
            )

        with patch.object(server, "llm_complete", fake_llm_complete):
            v1 = run(server.analyze_test_context(server.ContextAnalyzeBody()))

        item_id = v1.items[0].id
        v2 = run(
            server.patch_test_context(
                server.ContextPatchBody(item_updates=[server.ContextItemUpdate(id=item_id, value="12 V +/- 10%")])
            )
        )

        self.assertEqual(v2.version, 2)
        updated = next(item for item in v2.items if item.id == item_id)
        self.assertEqual(updated.value, "12 V +/- 10%")
        self.assertEqual(updated.status, "confirmed")

    def test_patch_removes_a_fabricated_item(self):
        self._seed_requirement("REQ-001", "The zone controller shall withstand 12 V nominal supply.")

        async def fake_llm_complete(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps(
                {
                    "items": [
                        {"category": "parameter", "key": "voltage", "value": "12 V", "source_requirement_ids": ["REQ-001"]},
                        {"category": "interface", "key": "SPI", "value": "SPI interface", "source_requirement_ids": []},
                    ],
                    "questions": [],
                }
            )

        with patch.object(server, "llm_complete", fake_llm_complete):
            v1 = run(server.analyze_test_context(server.ContextAnalyzeBody()))

        self.assertEqual(len(v1.items), 2)
        fabricated_id = next(item.id for item in v1.items if item.key == "SPI")

        v2 = run(server.patch_test_context(server.ContextPatchBody(item_removals=[fabricated_id])))

        self.assertEqual(v2.version, 2)
        self.assertEqual(len(v2.items), 1)
        self.assertNotIn(fabricated_id, [item.id for item in v2.items])

    def test_patch_removal_of_unknown_item_404s(self):
        self._seed_requirement("REQ-001", "The zone controller shall withstand 12 V nominal supply.")

        async def fake_llm_complete(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps({"items": [], "questions": []})

        with patch.object(server, "llm_complete", fake_llm_complete):
            run(server.analyze_test_context(server.ContextAnalyzeBody()))

        with self.assertRaises(HTTPException) as ctx:
            run(server.patch_test_context(server.ContextPatchBody(item_removals=["nope"])))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_patch_answers_question_and_adds_context_item(self):
        self._seed_requirement("REQ-001", "The zone controller shall log all authentication failures.")

        async def fake_llm_complete(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps(
                {"items": [], "questions": [{"question": "What test rig is available?", "reason": "needed"}]}
            )

        with patch.object(server, "llm_complete", fake_llm_complete):
            v1 = run(server.analyze_test_context(server.ContextAnalyzeBody()))

        question_id = v1.questions[0].id
        v2 = run(
            server.patch_test_context(
                server.ContextPatchBody(
                    question_answers=[server.ContextQuestionAnswer(question_id=question_id, answer="Bench with PSU.")]
                )
            )
        )

        answered = next(q for q in v2.questions if q.id == question_id)
        self.assertEqual(answered.status, "answered")
        self.assertEqual(answered.answer, "Bench with PSU.")
        self.assertTrue(any(item.value == "Bench with PSU." for item in v2.items))

    def test_get_questions_filters_by_status(self):
        self._seed_requirement("REQ-001", "The zone controller shall log all authentication failures.")

        async def fake_llm_complete(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps(
                {
                    "items": [],
                    "questions": [
                        {"question": "Q1?", "reason": "r1"},
                        {"question": "Q2?", "reason": "r2"},
                    ],
                }
            )

        with patch.object(server, "llm_complete", fake_llm_complete):
            v1 = run(server.analyze_test_context(server.ContextAnalyzeBody()))

        run(
            server.patch_test_context(
                server.ContextPatchBody(
                    question_answers=[server.ContextQuestionAnswer(question_id=v1.questions[0].id, answer="answered")]
                )
            )
        )

        open_questions = run(server.get_test_context_questions(status="open"))
        answered_questions = run(server.get_test_context_questions(status="answered"))
        self.assertEqual(len(open_questions), 1)
        self.assertEqual(len(answered_questions), 1)


if __name__ == "__main__":
    unittest.main()
