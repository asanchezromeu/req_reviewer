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
        NEEDS_INPUT_MARKER,
    )
except ImportError:
    import server
    from retrieval import Requirement, RequirementStore
    from testgen_lint import check_anti_genericity
    from testgen_prompts import (
        CLASSIFY_AND_ASSESS_PROMPT,
        GENERATE_TEST_CASE_PROMPT,
        AUTHORIZE_FILL_PROMPT,
        NEEDS_INPUT_MARKER,
    )


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

    def test_missing_measure_not_required_when_only_an_unrelated_context_item_has_one(self):
        # Regression test for a real live-testing finding: an unrelated context item
        # (e.g. a voltage value from a different requirement entirely) used to force
        # every requirement in the project to require a numeric acceptance criterion,
        # even a plain state-transition requirement with no inherent quantity.
        generated = {
            "preconditions": ["The PBDU has just powered on."],
            "acceptance_criteria": ["The PBDU enters the Initialization state."],
            "verification_method": "test",
        }
        context_items = [{"category": "parameter", "key": "nominal_supply_voltage", "value": "12 V"}]
        violations = check_anti_genericity(
            generated, "The PBDU shall enter Initialization state after power-up.", context_items, []
        )
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

    def test_self_flagged_placeholder_line_not_double_flagged(self):
        # A line the model already honestly marked as missing shouldn't also trip the
        # generic-wording, missing-measure, or untraceable-value lint - that would just be a
        # second, differently-worded flag for the exact same thing.
        generated = {
            "preconditions": [f"Bench at {NEEDS_INPUT_MARKER}supply voltage]."],
            "acceptance_criteria": [f"Response time <= {NEEDS_INPUT_MARKER}response time threshold]."],
            "verification_method": "test",
        }
        violations = check_anti_genericity(generated, "The system shall respond within 200 ms.", [], [])
        self.assertEqual(violations, [])


class TestgenResolveTests(unittest.TestCase):
    def setUp(self):
        # Isolate from the real, persistent-file-backed requirements_store,
        # same pattern as test_testgen_context.py / test_testgen_generation.py.
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = RequirementStore(Path(self.temp_dir.name) / "requirements.db")
        self.store_patcher = patch.object(server, "requirements_store", self.store)
        self.store_patcher.start()
        # Isolate from the real, committed backend/data/supporting_info.json too.
        self.supporting_info_path = Path(self.temp_dir.name) / "supporting_info.json"
        self.supporting_info_patcher = patch.object(server, "SUPPORTING_INFO_PATH", self.supporting_info_path)
        self.supporting_info_patcher.start()

    def tearDown(self):
        self.store_patcher.stop()
        self.supporting_info_patcher.stop()
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
        # sufficient, and generation invented an unsupported "200 ms" criterion. Verdict 2
        # no longer discards the test case for this - it flags the untraceable value and
        # keeps the (otherwise usable) generated content.
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
        self.assertEqual(result["status"], "generated")
        self.assertTrue(any(g["gap_source"] == "self_review" for g in result["test_case"].open_gaps))
        self.assertEqual(len(run(server.list_test_cases())), 1)

    def test_generation_self_reported_open_gaps_are_persisted_and_attached(self):
        # The generate call itself can now flag something it noticed, distinct from what
        # Verdict 1 already caught - persisted with gap_source "generation".
        self._seed_requirement("REQ-002", "The system shall be fast.")

        async def fake(provider, model, sys_msg, user, ollama_url=None):
            if sys_msg == CLASSIFY_AND_ASSESS_PROMPT:
                return json.dumps({"category": "Performance / timing", "sufficient": True, "gaps": []})
            return json.dumps(
                {
                    "preconditions": ["Nominal operating conditions."],
                    "steps": ["Trigger the operation.", "Measure response time."],
                    "acceptance_criteria": ["Response time <= [NEEDS INPUT: response time threshold]."],
                    "verification_method": "test",
                    "open_gaps": [{"item": "response time threshold", "why": "not stated anywhere"}],
                }
            )

        with patch.object(server, "llm_complete", fake):
            response = run(server.generate_test_cases(server.GenerateBody()))

        result = response["results"][0]
        self.assertEqual(result["status"], "generated")
        open_gaps = result["test_case"].open_gaps
        self.assertEqual(len(open_gaps), 1)
        self.assertEqual(open_gaps[0]["gap_source"], "generation")
        self.assertTrue(open_gaps[0]["gap_id"])

        # Also persisted to the project-wide worklist, resolvable/dismissable like any other gap.
        gaps = run(server.list_gaps(status="open"))
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["gap_source"], "generation")

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
        gap_id = response["results"][0]["test_case"].open_gaps[0]["gap_id"]

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
        gap_id = response["results"][0]["test_case"].open_gaps[0]["gap_id"]

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
        gap_id = response["results"][0]["test_case"].open_gaps[0]["gap_id"]

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
        gap_id = response["results"][0]["test_case"].open_gaps[0]["gap_id"]

        async def fake_malformed(provider, model, sys_msg, user, ollama_url=None):
            return "not json"

        with patch.object(server, "llm_complete", fake_malformed):
            result = run(server.resolve_gap(server.ResolveBody(gap_id=gap_id, resolution_type="authorize_fill")))

        self.assertEqual(result["status"], "needs_input")
        # The initial generate_test_cases call above already persisted one flagged test case
        # (insufficiency no longer blocks generation) - the authorize_fill proposal itself
        # failing just means no *additional* test case gets created.
        self.assertEqual(len(run(server.list_test_cases())), 1)

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
        gap_id = response["results"][0]["test_case"].open_gaps[0]["gap_id"]

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
        gap_id = response["results"][0]["test_case"].open_gaps[0]["gap_id"]

        open_gaps = run(server.list_gaps(status="open"))
        self.assertEqual(len(open_gaps), 1)

        fake_resolve = self._fake_llm(
            assess_response={"category": "Performance / timing", "sufficient": False, "gaps": [{"item": "x", "why": "y"}]},
            generate_response={
                "preconditions": ["p"],
                "steps": ["s"],
                "acceptance_criteria": [f"Response time <= {NEEDS_INPUT_MARKER}response time threshold]."],
                "verification_method": "test",
            },
        )
        with patch.object(server, "llm_complete", fake_resolve):
            run(server.resolve_gap(server.ResolveBody(gap_id=gap_id, resolution_type="answer", answer="150 ms")))

        self.assertEqual(len(run(server.list_gaps(status="open"))), 1)  # fresh gap from the regeneration attempt
        self.assertEqual(len(run(server.list_gaps(status="resolved"))), 1)

    # ---------- DELETE /testgen/gaps/{gap_id} (dismiss) ----------

    def test_dismiss_gap_removes_it_from_open_without_regenerating(self):
        self._seed_requirement("REQ-002", "The system shall be fast.")

        async def fake_insufficient(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps({"category": "Performance / timing", "sufficient": False, "gaps": [{"item": "x", "why": "y"}]})

        with patch.object(server, "llm_complete", fake_insufficient):
            response = run(server.generate_test_cases(server.GenerateBody()))
        gap_id = response["results"][0]["test_case"].open_gaps[0]["gap_id"]

        async def fail_if_called(*args, **kwargs):
            raise AssertionError("dismiss must not call the LLM")

        with patch.object(server, "llm_complete", fail_if_called):
            result = run(server.dismiss_gap(gap_id))

        self.assertEqual(result, {"dismissed": True})
        self.assertEqual(run(server.list_gaps(status="open")), [])
        dismissed = run(server.list_gaps(status="dismissed"))
        self.assertEqual(len(dismissed), 1)
        self.assertEqual(dismissed[0]["id"], gap_id)

    def test_dismiss_unknown_gap_404s(self):
        with self.assertRaises(HTTPException) as ctx:
            run(server.dismiss_gap("nope"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_dismiss_already_resolved_gap_409s(self):
        self._seed_requirement("REQ-002", "The system shall be fast.")

        async def fake_insufficient(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps({"category": "Performance / timing", "sufficient": False, "gaps": [{"item": "x", "why": "y"}]})

        with patch.object(server, "llm_complete", fake_insufficient):
            response = run(server.generate_test_cases(server.GenerateBody()))
        gap_id = response["results"][0]["test_case"].open_gaps[0]["gap_id"]
        run(server.dismiss_gap(gap_id))

        with self.assertRaises(HTTPException) as ctx:
            run(server.dismiss_gap(gap_id))
        self.assertEqual(ctx.exception.status_code, 409)

    # ---------- persistent supporting-info file ----------

    def test_resolve_answer_auto_appends_supporting_info_fact(self):
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
        gap_id = response["results"][0]["test_case"].open_gaps[0]["gap_id"]

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
            run(
                server.resolve_gap(
                    server.ResolveBody(gap_id=gap_id, resolution_type="answer", answer="150 ms maximum response time")
                )
            )

        facts = run(server.list_supporting_info())
        self.assertEqual(len(facts), 1)
        self.assertIn("150 ms maximum response time", facts[0]["text"])
        self.assertEqual(facts[0]["source"], "resolve_answer")
        self.assertEqual(facts[0]["requirement_id"], "REQ-002")

    def test_resolve_authorize_fill_does_not_auto_append_supporting_info(self):
        # authorize_fill is an engine guess, not a confirmed human fact - it must stay
        # ephemeral (per-test-case assumption only) until explicitly promoted via
        # POST /testgen/supporting-info/confirm.
        self._seed_requirement("REQ-002", "The system shall be fast.")

        async def fake_insufficient(provider, model, sys_msg, user, ollama_url=None):
            return json.dumps(
                {"category": "Performance / timing", "sufficient": False, "gaps": [{"item": "x", "why": "y"}]}
            )

        with patch.object(server, "llm_complete", fake_insufficient):
            response = run(server.generate_test_cases(server.GenerateBody()))
        gap_id = response["results"][0]["test_case"].open_gaps[0]["gap_id"]

        async def fake_resolve(provider, model, sys_msg, user, ollama_url=None):
            if sys_msg == AUTHORIZE_FILL_PROMPT:
                return json.dumps({"value": "200 ms", "rationale": "typical bound", "confidence": "medium"})
            if sys_msg == CLASSIFY_AND_ASSESS_PROMPT:
                return json.dumps({"category": "Performance / timing", "sufficient": True, "gaps": []})
            return json.dumps(
                {
                    "preconditions": ["Nominal operating conditions."],
                    "steps": ["Trigger.", "Measure."],
                    "acceptance_criteria": ["Response time <= 200 ms."],
                    "verification_method": "test",
                }
            )

        with patch.object(server, "llm_complete", fake_resolve):
            run(server.resolve_gap(server.ResolveBody(gap_id=gap_id, resolution_type="authorize_fill")))

        self.assertEqual(run(server.list_supporting_info()), [])

    def test_generate_injects_supporting_info_into_both_prompts(self):
        self._seed_requirement("REQ-002", "The system shall be fast.")
        run(
            server.confirm_supporting_info(
                server.SupportingInfoConfirmBody(
                    text="All electrical faults are simulated with electronic loads or fault injection."
                )
            )
        )
        seen_messages = []

        async def fake(provider, model, sys_msg, user, ollama_url=None):
            seen_messages.append(user)
            if sys_msg == CLASSIFY_AND_ASSESS_PROMPT:
                return json.dumps({"category": "Performance / timing", "sufficient": True, "gaps": []})
            return json.dumps(
                {
                    "preconditions": ["Nominal operating conditions."],
                    "steps": ["Trigger.", "Measure."],
                    "acceptance_criteria": ["Response time <= 200 ms."],
                    "verification_method": "test",
                }
            )

        with patch.object(server, "llm_complete", fake):
            run(server.generate_test_cases(server.GenerateBody()))

        self.assertEqual(len(seen_messages), 2)
        for message in seen_messages:
            self.assertIn("electronic loads or fault injection", message)

    def test_supporting_info_confirm_list_and_delete(self):
        fact = run(
            server.confirm_supporting_info(
                server.SupportingInfoConfirmBody(text="A test-only default fact.", requirement_id="REQ-002")
            )
        )
        self.assertEqual(fact["source"], "manual")
        self.assertEqual(run(server.list_supporting_info()), [fact])

        run(server.delete_supporting_info(fact["id"]))
        self.assertEqual(run(server.list_supporting_info()), [])

    def test_delete_unknown_supporting_info_404s(self):
        with self.assertRaises(HTTPException) as ctx:
            run(server.delete_supporting_info("nope"))
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
