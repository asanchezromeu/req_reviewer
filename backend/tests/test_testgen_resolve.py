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
    from backend.testgen_lint import check_anti_genericity
    from backend.testgen_prompts import (
        CLASSIFY_AND_ASSESS_PROMPT,
        GENERATE_TEST_CASE_PROMPT,
        AUTHORIZE_FILL_PROMPT,
    )
except ImportError:
    import server
    from retrieval import Requirement, RequirementStore
    from testgen_lint import check_anti_genericity
    from testgen_prompts import CLASSIFY_AND_ASSESS_PROMPT, GENERATE_TEST_CASE_PROMPT, AUTHORIZE_FILL_PROMPT


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class AntiGenericityLintTests(unittest.TestCase):
    """Pure unit tests against testgen_lint.check_anti_genericity - no FastAPI/LLM involved."""

    def test_banned_generic_phrase_flagged(self):
        generated = {
            "preconditions": ["Power on the device."],
            "acceptance_criteria": ["Response time <= 200 ms."],
            "verification_method": "test",
        }
        violations = check_anti_genericity(generated, "The system shall respond within 200 ms.", [], [])
        self.assertTrue(any("power on the device" in v["item"].lower() for v in violations))

    def test_missing_measure_flagged_when_requirement_carries_a_quantity(self):
        generated = {
            "preconditions": ["Nominal load applied."],
            "acceptance_criteria": ["The response is correct."],
            "verification_method": "test",
        }
        violations = check_anti_genericity(generated, "The system shall respond within 200 ms.", [], [])
        self.assertTrue(any("measurable quantity" in v["why"] for v in violations))

    def test_missing_measure_not_required_for_non_test_method(self):
        generated = {
            "preconditions": ["Design document available for review."],
            "acceptance_criteria": ["The design rationale is documented and reviewed."],
            "verification_method": "inspection",
        }
        violations = check_anti_genericity(generated, "The system shall respond within 200 ms.", [], [])
        self.assertEqual(violations, [])

    def test_missing_measure_not_required_when_requirement_has_no_quantity(self):
        generated = {
            "preconditions": ["System is powered and idle."],
            "acceptance_criteria": ["All authentication failures appear in the log."],
            "verification_method": "test",
        }
        violations = check_anti_genericity(generated, "The system shall log all authentication failures.", [], [])
        self.assertEqual(violations, [])

    def test_untraceable_numeric_value_flagged(self):
        generated = {
            "preconditions": ["Bench with regulated PSU at 12 V."],
            "acceptance_criteria": ["Response time <= 200 ms."],
            "verification_method": "test",
        }
        violations = check_anti_genericity(generated, "The system shall respond within 200 ms.", [], [])
        self.assertTrue(any("12 V" in v["why"] or "12v" in v["why"].lower() for v in violations))

    def test_traceable_value_with_whitespace_difference_not_flagged(self):
        generated = {
            "preconditions": ["Supply at 12V nominal."],
            "acceptance_criteria": ["Response time <= 200 ms."],
            "verification_method": "test",
        }
        violations = check_anti_genericity(
            generated, "The system shall respond within 200 ms with a supply of 12 V.", [], []
        )
        self.assertEqual(violations, [])

    def test_value_grounded_in_context_item_not_flagged(self):
        generated = {
            "preconditions": ["Bench supply set to 12 V per project test context."],
            "acceptance_criteria": ["Response time <= 200 ms."],
            "verification_method": "test",
        }
        context_items = [{"category": "parameter", "key": "supply voltage", "value": "12 V nominal"}]
        violations = check_anti_genericity(
            generated, "The system shall respond within 200 ms.", context_items, []
        )
        self.assertEqual(violations, [])

    def test_value_grounded_in_assumption_not_flagged(self):
        generated = {
            "preconditions": ["Repeat the test 5 times."],
            "acceptance_criteria": ["Response time <= 200 ms."],
            "verification_method": "test",
        }
        assumptions = [{"text": "repetition count", "value": "5 repetitions", "rationale": "typical bench practice"}]
        violations = check_anti_genericity(
            generated, "The system shall respond within 200 ms.", [], assumptions
        )
        self.assertEqual(violations, [])

    def test_clean_grounded_output_has_no_violations(self):
        generated = {
            "preconditions": ["Nominal load applied to the zone controller."],
            "acceptance_criteria": ["Response time <= 200 ms."],
            "verification_method": "test",
        }
        violations = check_anti_genericity(generated, "The zone controller shall respond within 200 ms.", [], [])
        self.assertEqual(violations, [])


class TestgenResolveTests(unittest.TestCase):
    def setUp(self):
        # Isolate from the real, persistent-file-backed requirements_store,
        # same pattern as test_testgen_context.py / test_testgen_generation.py.
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

    # ---------- gate honesty via the full pipeline ----------

    def test_verdict_2_catches_invented_value_verdict_1_missed(self):
        # Regression test for the live-test finding: a vague requirement was judged
        # sufficient, and generation invented an unsupported "200 ms" criterion.
        self._seed_requirement("REQ-002", "The system shall be fast.")

        async def fake(provider, model, sys_msg, user, ollama_url=None):
            if sys_msg == CLASSIFY_AND_ASSESS_PROMPT:
                return json.dumps({"category": "Performance / timing", "sufficient": True, "gaps": []})
            return json.dumps(
                {
                    "preconditions": ["Nominal operating conditions."],
                    "steps": ["Trigger the operation.", "Measure response time."],
                    "acceptance_criteria": ["Response time <= 200 ms."],
                    "verification_method": "test",
                }
            )

        with patch.object(server, "llm_complete", fake):
            response = run(server.generate_test_cases(server.GenerateBody()))

        result = response["results"][0]
        self.assertEqual(result["status"], "needs_input")
        self.assertTrue(any(g["gap_source"] == "self_review" for g in result["gaps"]))
        self.assertEqual(run(server.list_test_cases()), [])

    def test_traceable_generation_persists_with_empty_assumptions(self):
        self._seed_requirement("REQ-001", "The zone controller shall respond within 200 ms.")
        fake = self._fake_llm(
            assess_response={"category": "Performance / timing", "sufficient": True, "gaps": []},
            generate_response={
                "preconditions": ["Nominal load applied to the zone controller."],
                "steps": ["Send request.", "Measure response time."],
                "acceptance_criteria": ["Response time <= 200 ms."],
                "verification_method": "test",
            },
        )
        with patch.object(server, "llm_complete", fake):
            response = run(server.generate_test_cases(server.GenerateBody()))

        result = response["results"][0]
        self.assertEqual(result["status"], "generated")
        self.assertEqual(result["test_case"].assumptions, [])

    def test_method_routing_inspection_fixture_passes_verdict_2(self):
        self._seed_requirement("REQ-004", "The design shall follow the documented coding standard.")
        fake = self._fake_llm(
            assess_response={"category": "Non-testable-by-test", "sufficient": True, "gaps": []},
            generate_response={
                "preconditions": ["Source code and coding standard document available."],
                "steps": ["Review the code against the coding standard checklist."],
                "acceptance_criteria": ["No unresolved deviations from the coding standard remain."],
                "verification_method": "inspection",
            },
        )
        with patch.object(server, "llm_complete", fake):
            response = run(server.generate_test_cases(server.GenerateBody()))

        result = response["results"][0]
        self.assertEqual(result["status"], "generated")
        self.assertEqual(result["test_case"].verification_method, "inspection")

    def _fake_llm(self, assess_response, generate_response=None):
        async def fake(provider, model, sys_msg, user, ollama_url=None):
            if sys_msg == CLASSIFY_AND_ASSESS_PROMPT:
                return json.dumps(assess_response)
            if sys_msg == GENERATE_TEST_CASE_PROMPT:
                return json.dumps(generate_response)
            raise AssertionError(f"unexpected system prompt: {sys_msg[:40]}")

        return fake

    # ---------- resolve: answer path ----------

    def test_resolve_answer_bumps_context_and_regenerates(self):
        self._seed_requirement("REQ-002", "The system shall be fast.")

        async def fake_insufficient(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps(
                {
                    "category": "Performance / timing",
                    "sufficient": False,
                    "gaps": [{"item": "response time threshold", "why": "no numeric value given"}],
                }
            )

        with patch.object(server, "llm_complete", fake_insufficient):
            response = run(server.generate_test_cases(server.GenerateBody()))
        gap_id = response["results"][0]["gaps"][0]["gap_id"]

        fake_resolve = self._fake_llm(
            assess_response={"category": "Performance / timing", "sufficient": True, "gaps": []},
            generate_response={
                "preconditions": ["Nominal operating conditions."],
                "steps": ["Trigger the operation.", "Measure response time."],
                "acceptance_criteria": ["Response time <= 150 ms."],
                "verification_method": "test",
            },
        )
        with patch.object(server, "llm_complete", fake_resolve):
            result = run(
                server.resolve_gap(
                    server.ResolveBody(gap_id=gap_id, resolution_type="answer", answer="150 ms maximum response time")
                )
            )

        self.assertEqual(result["status"], "generated")
        context = run(server.get_current_test_context())
        # No context existed before this resolve call, so the answer path creates version 1.
        self.assertEqual(context["version"], 1)
        self.assertTrue(any("150 ms" in item["value"] for item in context["items"]))

        gaps = run(server.list_gaps())
        self.assertEqual(gaps[0]["status"], "resolved")

    def test_resolve_answer_requires_answer_field(self):
        self._seed_requirement("REQ-002", "The system shall be fast.")

        async def fake_insufficient(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps({"category": "Performance / timing", "sufficient": False, "gaps": [{"item": "x", "why": "y"}]})

        with patch.object(server, "llm_complete", fake_insufficient):
            response = run(server.generate_test_cases(server.GenerateBody()))
        gap_id = response["results"][0]["gaps"][0]["gap_id"]

        with self.assertRaises(HTTPException) as ctx:
            run(server.resolve_gap(server.ResolveBody(gap_id=gap_id, resolution_type="answer")))
        self.assertEqual(ctx.exception.status_code, 400)

    # ---------- resolve: authorize_fill path ----------

    def test_resolve_authorize_fill_populates_assumptions(self):
        self._seed_requirement("REQ-002", "The system shall be fast.")

        async def fake_insufficient(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps(
                {
                    "category": "Performance / timing",
                    "sufficient": False,
                    "gaps": [{"item": "response time threshold", "why": "no numeric value given"}],
                }
            )

        with patch.object(server, "llm_complete", fake_insufficient):
            response = run(server.generate_test_cases(server.GenerateBody()))
        gap_id = response["results"][0]["gaps"][0]["gap_id"]

        async def fake_resolve(provider, model, sys_msg, user, ollama_url=None):
            if sys_msg == AUTHORIZE_FILL_PROMPT:
                return json.dumps(
                    {
                        "value": "200 ms maximum response time",
                        "rationale": "Typical automotive diagnostic response bound.",
                        "confidence": "medium",
                    }
                )
            if sys_msg == CLASSIFY_AND_ASSESS_PROMPT:
                return json.dumps({"category": "Performance / timing", "sufficient": True, "gaps": []})
            return json.dumps(
                {
                    "preconditions": ["Nominal operating conditions."],
                    "steps": ["Trigger the operation.", "Measure response time."],
                    "acceptance_criteria": ["Response time <= 200 ms."],
                    "verification_method": "test",
                }
            )

        with patch.object(server, "llm_complete", fake_resolve):
            result = run(server.resolve_gap(server.ResolveBody(gap_id=gap_id, resolution_type="authorize_fill")))

        self.assertEqual(result["status"], "generated")
        assumptions = result["test_case"].assumptions
        self.assertEqual(len(assumptions), 1)
        self.assertEqual(assumptions[0]["value"], "200 ms maximum response time")
        self.assertEqual(assumptions[0]["confidence"], "medium")

    def test_resolve_authorize_fill_malformed_proposal_degrades_safely(self):
        self._seed_requirement("REQ-002", "The system shall be fast.")

        async def fake_insufficient(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps(
                {"category": "Performance / timing", "sufficient": False, "gaps": [{"item": "x", "why": "y"}]}
            )

        with patch.object(server, "llm_complete", fake_insufficient):
            response = run(server.generate_test_cases(server.GenerateBody()))
        gap_id = response["results"][0]["gaps"][0]["gap_id"]

        async def fake_malformed(provider, model, sys_msg, user, ollama_url=None):
            return "not json"

        with patch.object(server, "llm_complete", fake_malformed):
            result = run(server.resolve_gap(server.ResolveBody(gap_id=gap_id, resolution_type="authorize_fill")))

        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(run(server.list_test_cases()), [])

    # ---------- resolve: error paths ----------

    def test_resolve_unknown_gap_id_404s(self):
        with self.assertRaises(HTTPException) as ctx:
            run(server.resolve_gap(server.ResolveBody(gap_id="nope", resolution_type="answer", answer="x")))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_resolve_already_resolved_gap_409s(self):
        self._seed_requirement("REQ-002", "The system shall be fast.")

        async def fake_insufficient(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps({"category": "Performance / timing", "sufficient": False, "gaps": [{"item": "x", "why": "y"}]})

        with patch.object(server, "llm_complete", fake_insufficient):
            response = run(server.generate_test_cases(server.GenerateBody()))
        gap_id = response["results"][0]["gaps"][0]["gap_id"]

        fake_resolve = self._fake_llm(
            assess_response={"category": "Performance / timing", "sufficient": False, "gaps": [{"item": "x", "why": "y"}]}
        )
        with patch.object(server, "llm_complete", fake_resolve):
            run(server.resolve_gap(server.ResolveBody(gap_id=gap_id, resolution_type="answer", answer="150 ms")))

        with self.assertRaises(HTTPException) as ctx:
            run(server.resolve_gap(server.ResolveBody(gap_id=gap_id, resolution_type="answer", answer="150 ms")))
        self.assertEqual(ctx.exception.status_code, 409)

    # ---------- GET /testgen/gaps ----------

    def test_list_gaps_filters_by_status(self):
        self._seed_requirement("REQ-002", "The system shall be fast.")

        async def fake_insufficient(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps({"category": "Performance / timing", "sufficient": False, "gaps": [{"item": "x", "why": "y"}]})

        with patch.object(server, "llm_complete", fake_insufficient):
            response = run(server.generate_test_cases(server.GenerateBody()))
        gap_id = response["results"][0]["gaps"][0]["gap_id"]

        open_gaps = run(server.list_gaps(status="open"))
        self.assertEqual(len(open_gaps), 1)

        fake_resolve = self._fake_llm(
            assess_response={"category": "Performance / timing", "sufficient": False, "gaps": [{"item": "x", "why": "y"}]}
        )
        with patch.object(server, "llm_complete", fake_resolve):
            run(server.resolve_gap(server.ResolveBody(gap_id=gap_id, resolution_type="answer", answer="150 ms")))

        self.assertEqual(len(run(server.list_gaps(status="open"))), 1)  # fresh gap from the regeneration attempt
        self.assertEqual(len(run(server.list_gaps(status="resolved"))), 1)


if __name__ == "__main__":
    unittest.main()
