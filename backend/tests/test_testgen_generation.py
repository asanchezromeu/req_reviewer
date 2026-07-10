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
    from backend.testgen_prompts import CLASSIFY_AND_ASSESS_PROMPT, GENERATE_TEST_CASE_PROMPT
except ImportError:
    import server
    from retrieval import Requirement, RequirementStore
    from testgen_prompts import CLASSIFY_AND_ASSESS_PROMPT, GENERATE_TEST_CASE_PROMPT


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestgenGenerationTests(unittest.TestCase):
    def setUp(self):
        # Isolate from the real, persistent-file-backed requirements_store,
        # same as test_testgen_context.py.
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = RequirementStore(Path(self.temp_dir.name) / "requirements.db")
        self.store_patcher = patch.object(server, "requirements_store", self.store)
        self.store_patcher.start()

    def tearDown(self):
        self.store_patcher.stop()
        self.temp_dir.cleanup()
        for collection in (server.db.category_strategies, server.db.test_cases, server.db.test_gaps):
            for doc in run(collection.find({}, {"_id": 0}).to_list(1000)):
                key = "category" if "category" in doc else "id"
                run(collection.delete_one({key: doc[key]}))
        while True:
            doc = run(server.get_current_test_context())
            if not doc:
                break
            run(server.db.test_context_versions.delete_one({"id": doc["id"]}))

    def _seed_requirement(self, req_id, text):
        self.store.replace_requirements([Requirement(id=req_id, text=text)])

    def _seed_requirements(self, pairs):
        self.store.replace_requirements([Requirement(id=req_id, text=text) for req_id, text in pairs])

    # ---------- category strategies ----------

    def test_defaults_returned_for_untouched_categories(self):
        strategies = run(server.list_category_strategies())
        by_name = {s["category"]: s["instructions"] for s in strategies}
        self.assertIn("Electrical", by_name)
        self.assertIn("Safety-related", by_name)

    def test_put_overrides_a_category_and_delete_reverts_it(self):
        original = dict(run(server.get_category_strategies()))["Electrical"]

        run(server.set_category_strategy("Electrical", server.CategoryStrategyBody(instructions="Custom text.")))
        overridden = dict(run(server.get_category_strategies()))["Electrical"]
        self.assertEqual(overridden, "Custom text.")

        run(server.delete_category_strategy("Electrical"))
        reverted = dict(run(server.get_category_strategies()))["Electrical"]
        self.assertEqual(reverted, original)

    def test_can_add_wholly_new_category(self):
        run(server.set_category_strategy("Thermal", server.CategoryStrategyBody(instructions="Thermal cycling steps.")))
        strategies = dict(run(server.get_category_strategies()))
        self.assertEqual(strategies["Thermal"], "Thermal cycling steps.")

    def test_delete_unknown_override_404s(self):
        with self.assertRaises(HTTPException) as ctx:
            run(server.delete_category_strategy("NeverSet"))
        self.assertEqual(ctx.exception.status_code, 404)

    # ---------- generation ----------

    def _fake_llm(self, assess_response, generate_response=None):
        async def fake(provider, model, sys_msg, user, ollama_url=None):
            if sys_msg == CLASSIFY_AND_ASSESS_PROMPT:
                return json.dumps(assess_response)
            if sys_msg == GENERATE_TEST_CASE_PROMPT:
                return json.dumps(generate_response)
            raise AssertionError(f"unexpected system prompt: {sys_msg[:40]}")

        return fake

    def test_sufficient_requirement_produces_persisted_test_case(self):
        self._seed_requirement("REQ-001", "The zone controller shall respond within 200 ms under nominal load.")
        fake = self._fake_llm(
            assess_response={"category": "Performance / timing", "sufficient": True, "gaps": []},
            generate_response={
                "preconditions": ["Nominal load applied to the zone controller."],
                "steps": ["Send diagnostic request.", "Measure response time."],
                "acceptance_criteria": ["Response time <= 200 ms."],
                "verification_method": "test",
            },
        )

        with patch.object(server, "llm_complete", fake):
            response = run(server.generate_test_cases(server.GenerateBody()))

        result = response["results"][0]
        self.assertEqual(result["status"], "generated")
        self.assertEqual(result["category"], "Performance / timing")
        self.assertEqual(result["test_case"].requirement_ids, ["REQ-001"])
        self.assertEqual(result["test_case"].verification_method, "test")

        persisted = run(server.list_test_cases())
        self.assertEqual(len(persisted), 1)

    def test_insufficient_requirement_still_generates_with_open_gaps(self):
        # Regression test: insufficiency used to block generation entirely, which combined
        # with a category needing several simultaneous facts (Safety-related) produced an
        # endless gap loop with nothing ever persisted. Now generation always proceeds and
        # the missing info is flagged on the test case itself instead of blocking it.
        self._seed_requirement("REQ-001", "The system shall be fast.")
        fake = self._fake_llm(
            assess_response={
                "category": "Performance / timing",
                "sufficient": False,
                "gaps": [{"item": "response time threshold", "why": "no numeric value given"}],
            },
            generate_response={
                "preconditions": ["Nominal operating conditions."],
                "steps": ["Trigger the operation.", "Measure response time."],
                "acceptance_criteria": ["Response time <= [NEEDS INPUT: response time threshold]."],
                "verification_method": "test",
            },
        )

        with patch.object(server, "llm_complete", fake):
            response = run(server.generate_test_cases(server.GenerateBody()))

        result = response["results"][0]
        self.assertEqual(result["status"], "generated")
        open_gaps = result["test_case"].open_gaps
        self.assertEqual(len(open_gaps), 1)
        self.assertEqual(open_gaps[0]["gap_source"], "sufficiency")
        self.assertEqual(len(run(server.list_test_cases())), 1)

    def test_malformed_assess_output_degrades_to_needs_input(self):
        self._seed_requirement("REQ-001", "The system shall log failures.")

        async def fake(provider, model, sys_msg, user, ollama_url=None):
            return "not json"

        with patch.object(server, "llm_complete", fake):
            response = run(server.generate_test_cases(server.GenerateBody()))

        result = response["results"][0]
        self.assertEqual(result["status"], "needs_input")
        self.assertIsNone(result["category"])

    def test_malformed_generate_output_degrades_to_needs_input(self):
        self._seed_requirement("REQ-001", "The system shall log failures.")

        async def fake(provider, model, sys_msg, user, ollama_url=None):
            if sys_msg == CLASSIFY_AND_ASSESS_PROMPT:
                return json.dumps({"category": "Functional", "sufficient": True, "gaps": []})
            return "not json"

        with patch.object(server, "llm_complete", fake):
            response = run(server.generate_test_cases(server.GenerateBody()))

        result = response["results"][0]
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(run(server.list_test_cases()), [])

    def test_safety_related_gets_mandatory_review_flag(self):
        self._seed_requirement("REQ-001", "The zone controller shall enter a safe state on loss of CAN bus.")
        fake = self._fake_llm(
            assess_response={"category": "Safety-related", "sufficient": True, "gaps": []},
            generate_response={
                "preconditions": ["CAN bus connected and operating."],
                "steps": ["Disconnect CAN bus.", "Observe safe-state entry."],
                "acceptance_criteria": ["Safe state is entered, verified by monitoring the safe-state output signal."],
                "verification_method": "test",
            },
        )

        with patch.object(server, "llm_complete", fake):
            response = run(server.generate_test_cases(server.GenerateBody()))

        result = response["results"][0]
        self.assertEqual(result["test_case"].review_flags, ["safety"])

    def test_batch_mixed_results_one_failure_does_not_block_others(self):
        self._seed_requirements(
            [
                ("REQ-001", "The zone controller shall respond within 200 ms."),
                ("REQ-002", "The system shall be fast."),
                ("REQ-003", "The zone controller shall log all failures."),
            ]
        )

        async def fake(provider, model, sys_msg, user, ollama_url=None):
            if sys_msg == CLASSIFY_AND_ASSESS_PROMPT:
                if "REQ-001" in user:
                    return json.dumps({"category": "Performance / timing", "sufficient": True, "gaps": []})
                if "REQ-002" in user:
                    return json.dumps(
                        {"category": "Performance / timing", "sufficient": False, "gaps": [{"item": "x", "why": "y"}]}
                    )
                return "not json"  # REQ-003: malformed
            return json.dumps(
                {
                    "preconditions": ["p"],
                    "steps": ["s"],
                    "acceptance_criteria": ["Response time <= 200 ms."],
                    "verification_method": "test",
                }
            )

        with patch.object(server, "llm_complete", fake):
            response = run(server.generate_test_cases(server.GenerateBody()))

        by_id = {r["requirement_id"]: r for r in response["results"]}
        self.assertEqual(len(by_id), 3)
        self.assertEqual(by_id["REQ-001"]["status"], "generated")
        # REQ-002 is "insufficient" per the assess call, but that no longer blocks generation -
        # it still gets a test case, just with the gap flagged on it (plus a self-review flag,
        # since the shared fixture generate response invents a "200 ms" value REQ-002's own text
        # never states).
        self.assertEqual(by_id["REQ-002"]["status"], "generated")
        req002_gap_sources = {g["gap_source"] for g in by_id["REQ-002"]["test_case"].open_gaps}
        self.assertIn("sufficiency", req002_gap_sources)
        # REQ-003's assess call itself returned unparsable JSON - a genuine failure, still needs_input.
        self.assertEqual(by_id["REQ-003"]["status"], "needs_input")

    def test_generate_requires_matching_requirements(self):
        with self.assertRaises(HTTPException) as ctx:
            run(server.generate_test_cases(server.GenerateBody()))
        self.assertEqual(ctx.exception.status_code, 400)

    # ---------- testcases list ----------

    def test_list_test_cases_filters_by_requirement_id(self):
        self._seed_requirements(
            [
                ("REQ-001", "The zone controller shall respond within 200 ms."),
                ("REQ-002", "The zone controller shall log all failures."),
            ]
        )

        async def fake(provider, model, sys_msg, user, ollama_url=None):
            if sys_msg == CLASSIFY_AND_ASSESS_PROMPT:
                return json.dumps({"category": "Functional", "sufficient": True, "gaps": []})
            if "REQ-001" in user:
                return json.dumps(
                    {
                        "preconditions": ["p"],
                        "steps": ["s"],
                        "acceptance_criteria": ["Response time <= 200 ms."],
                        "verification_method": "test",
                    }
                )
            return json.dumps(
                {
                    "preconditions": ["p"],
                    "steps": ["s"],
                    "acceptance_criteria": ["All failures appear in the log."],
                    "verification_method": "test",
                }
            )

        with patch.object(server, "llm_complete", fake):
            run(server.generate_test_cases(server.GenerateBody()))

        all_cases = run(server.list_test_cases())
        self.assertEqual(len(all_cases), 2)
        filtered = run(server.list_test_cases(requirement_id="REQ-001"))
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["requirement_ids"], ["REQ-001"])


if __name__ == "__main__":
    unittest.main()
