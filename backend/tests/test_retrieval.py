import tempfile
import time
import unittest
from pathlib import Path

try:
    from backend.retrieval import (
        IndexCoordinator,
        Requirement,
        RequirementStore,
        broad_summary_sources,
        build_summary_context,
        compact_text,
        cosine_distance,
        extract_parameters,
        fallback_summary,
        extract_quantities,
        normalize_token,
        parse_import,
        ranked_matches,
        select_summary_sources,
        structural_score,
    )
except ImportError:
    from retrieval import (
        IndexCoordinator,
        Requirement,
        RequirementStore,
        broad_summary_sources,
        build_summary_context,
        compact_text,
        cosine_distance,
        extract_parameters,
        fallback_summary,
        extract_quantities,
        normalize_token,
        parse_import,
        ranked_matches,
        select_summary_sources,
        structural_score,
    )


class RequirementStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = RequirementStore(Path(self.temp_dir.name) / "requirements.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_replace_persists_and_only_invalidates_changed_rows(self):
        rows = [
            Requirement(id="REQ-001", text="The system shall encrypt data."),
            Requirement(id="REQ-002", text="The system shall log failures."),
        ]
        saved = self.store.replace_requirements(rows)
        self.assertEqual(2, len(saved))
        self.store.save_embeddings(
            "test-embed",
            saved,
            [[1.0, 0.0], [0.0, 1.0]],
        )
        self.assertEqual(2, self.store.counts("test-embed")["indexed"])

        changed = [
            Requirement(id="REQ-001", text="The system shall encrypt stored data."),
            Requirement(id="REQ-002", text="The system shall log failures."),
        ]
        self.store.replace_requirements(changed)
        stale = self.store.stale_requirements("test-embed")
        self.assertEqual(["REQ-001"], [item["id"] for item in stale])

    def test_background_indexer_builds_vectors(self):
        self.store.replace_requirements(
            [
                Requirement(id="REQ-001", text="security encryption"),
                Requirement(id="REQ-002", text="response performance"),
            ]
        )

        def fake_embedder(_url, _model, texts):
            return [
                [1.0, 0.0] if "security" in text else [0.0, 1.0]
                for text in texts
            ]

        indexer = IndexCoordinator(self.store, embedder=fake_embedder)
        indexer.schedule("test-embed", "http://ollama")
        deadline = time.time() + 2
        while indexer.status()["state"] == "indexing" and time.time() < deadline:
            time.sleep(0.01)

        status = indexer.status()
        self.assertEqual("ready", status["state"])
        self.assertEqual(2, status["indexed"])
        indexed = self.store.indexed_requirements("test-embed")
        query = [0.9, 0.1]
        ranked = sorted(indexed, key=lambda row: cosine_distance(query, row["vector"]))
        self.assertEqual("REQ-001", ranked[0]["id"])

    def test_json_and_csv_import(self):
        json_rows = parse_import(
            "requirements.json",
            b'{"requirements":[{"id":"REQ-A","text":"Alpha"}]}',
        )
        csv_rows = parse_import(
            "requirements.csv",
            b"id,requirement,source\nREQ-B,Beta,System spec\n",
        )
        self.assertEqual("REQ-A", json_rows[0].id)
        self.assertEqual("REQ-B", csv_rows[0].id)
        self.assertEqual("System spec", csv_rows[0].source)

    def test_ranked_matches_discards_weak_context(self):
        indexed = [
            {"id": "REQ-001", "text": "A", "source": None, "vector": [1.0, 0.0]},
            {"id": "REQ-002", "text": "B", "source": None, "vector": [0.97, 0.25]},
            {"id": "REQ-003", "text": "C", "source": None, "vector": [0.0, 1.0]},
        ]
        result = ranked_matches(indexed, [1.0, 0.0], min_similarity=0.30)

        self.assertEqual(["REQ-001", "REQ-002"], [item["id"] for item in result["matches"]])
        self.assertEqual(1, result["discarded"])
        self.assertGreaterEqual(result["matches"][0]["score"], 0.40)

    def test_hybrid_ranking_prefers_parameter_matches(self):
        indexed = [
            {
                "id": "REQ-073",
                "text": "In degraded mode, the system shall withstand a nominal current of 1A.",
                "source": None,
                "vector": [1.0, 0.0],
            },
            {
                "id": "PBDU-ELEC-100",
                "text": "The PDU shall withstand 20A nominal current during 100s.",
                "source": None,
                "vector": [0.99, 0.01],
            },
            {
                "id": "PBDU-ELEC-001",
                "text": "The PBDU shall operate from a nominal 12 V vehicle electrical supply.",
                "source": None,
                "vector": [0.98, 0.02],
            },
            {
                "id": "PBDU-MGMT-001",
                "text": "Each system requirement shall have a unique identifier.",
                "source": None,
                "vector": [0.97, 0.03],
            },
        ]

        result = ranked_matches(
            indexed,
            [1.0, 0.0],
            query="What nominal current shall the system withstand?",
            min_similarity=0.30,
        )

        ids = [item["id"] for item in result["matches"]]
        self.assertEqual(["REQ-073", "PBDU-ELEC-100"], ids)
        self.assertEqual(2, result["discarded"])

    def test_summary_context_is_bounded_for_pi_performance(self):
        matches = [
            {
                "id": f"REQ-{index:03d}",
                "text": "The requirement contains a very long explanation. " * 20,
                "score": 1.0 - index * 0.01,
            }
            for index in range(8)
        ]

        sources = select_summary_sources(matches, limit=4)
        context = build_summary_context(sources)

        self.assertEqual(4, len(sources))
        self.assertLess(len(context), 1200)
        self.assertIn("REQ-000", context)
        self.assertNotIn("REQ-004", context)

    def test_fallback_summary_cites_sources_without_llm(self):
        answer = fallback_summary(
            "What current is supported?",
            [
                {"id": "REQ-001", "text": "The system shall withstand 1A nominal current."},
                {"id": "REQ-002", "text": "The system shall withstand 20A current during 100s."},
            ],
            warning="timeout",
        )

        self.assertIn("REQ-001", answer)
        self.assertIn("REQ-002", answer)
        self.assertIn("no single unique current value", answer.lower())

    def test_fallback_summary_synthesizes_non_unique_current_values(self):
        answer = fallback_summary(
            "What is the nominal current?",
            [
                {"id": "REQ-073", "text": "In degraded mode, the system shall withstand a nominal current of 1A."},
                {"id": "PBDU-ELEC-100", "text": "The PDU shall withstand 20A nominal current during 100s."},
            ],
        )

        self.assertIn("no single unique current value", answer.lower())
        self.assertIn("1A", answer)
        self.assertIn("20A", answer)
        self.assertIn("REQ-073", answer)

    def test_extract_quantities_keeps_value_and_unit(self):
        quantities = extract_quantities("The PDU shall withstand 20A nominal current during 100s.")
        self.assertEqual(["20A", "100s"], [quantity["text"] for quantity in quantities])

    def test_compact_text_limits_long_requirements(self):
        self.assertEqual("short text", compact_text("short   text", limit=20))
        self.assertLessEqual(len(compact_text("x" * 500, limit=30)), 30)

    def test_broad_summary_prefers_product_features_over_meta_requirements(self):
        indexed = [
            {
                "id": "PBDU-MGMT-001",
                "text": "Each system requirement shall have a unique identifier.",
                "source": None,
                "vector": [1.0, 0.0],
            },
            {
                "id": "PBDU-TRACE-001",
                "text": "Each requirement shall be traceable to a stakeholder need.",
                "source": None,
                "vector": [0.99, 0.01],
            },
            {
                "id": "PBDU-ELEC-003",
                "text": "The PBDU shall protect its power outputs against short-circuit conditions.",
                "source": None,
                "vector": [0.90, 0.10],
            },
            {
                "id": "PBDU-VI-003",
                "text": "The PBDU shall measure the current consumed by each controlled power load channel.",
                "source": None,
                "vector": [0.89, 0.11],
            },
            {
                "id": "PBDU-ELEC-001",
                "text": "The PBDU shall operate from a nominal 12 V vehicle electrical supply.",
                "source": None,
                "vector": [0.88, 0.12],
            },
        ]

        result = broad_summary_sources(
            indexed,
            [1.0, 0.0],
            limit=3,
        )
        ids = [item["id"] for item in result["matches"]]
        answer = fallback_summary(
            "Explain the main features of the system under development",
            result["matches"],
        )

        self.assertIn("PBDU-ELEC-003", ids)
        self.assertIn("PBDU-VI-003", ids)
        self.assertNotIn("PBDU-MGMT-001", ids)
        self.assertIn("main product capabilities", answer)
        self.assertIn("short-circuit", answer)


class ParameterExtractionTests(unittest.TestCase):
    # Regression coverage for a real demo finding: a requirement that only states a value
    # via its unit ("12 V") was scoring structure=0.0 against a "nominal voltage" query,
    # while requirements merely mentioning the word "voltage" in passing scored 1.0 -
    # penalizing the requirement that actually answers the query.

    def test_unit_only_requirement_matches_parameter_noun_query(self):
        query = "nominal voltage"
        requirement_text = "The PBDU shall operate from a nominal 12 V vehicle electrical supply."
        self.assertEqual(structural_score(query, requirement_text), 1.0)

    def test_extract_parameters_derives_parameter_from_unit(self):
        self.assertIn("voltage", extract_parameters("The PBDU shall operate from a nominal 12 V supply."))
        self.assertIn("current", extract_parameters("The channel shall be limited to 2 A."))
        self.assertIn("temperature", extract_parameters("The PBDU shall operate up to 85 C."))

    def test_extract_parameters_still_finds_named_parameter_without_unit(self):
        self.assertIn("voltage", extract_parameters("The PBDU shall measure the supply input voltage."))

    def test_requirement_with_only_unmapped_unit_is_not_penalized_to_zero_for_unrelated_query(self):
        # A requirement stating a duration ("200 ms") shouldn't spuriously match a
        # voltage query just because both fall through the same code path.
        self.assertEqual(structural_score("nominal voltage", "The system shall respond within 200 ms."), 0.0)


class StemmingTests(unittest.TestCase):
    # Regression coverage for a real live-testing finding: "operating voltage" scored
    # zero keyword overlap against a requirement containing "operate", because naive
    # "-ing" suffix stripping doesn't reverse English's silent-e-drop spelling rule
    # ("operate" -> "operating" drops the e; stripping "ing" alone gives "operat", not
    # "operate"). This crushed an otherwise-best-matching candidate below the ranking
    # floor before it ever reached LLM verification.

    def test_ate_verb_family_matches_its_gerund(self):
        self.assertEqual(normalize_token("operating"), normalize_token("operate"))
        self.assertEqual(normalize_token("activating"), normalize_token("activate"))
        self.assertEqual(normalize_token("calibrating"), normalize_token("calibrate"))

    def test_doubled_consonant_before_ing_still_destems_correctly(self):
        self.assertEqual(normalize_token("running"), normalize_token("run"))

    def test_ordinary_ing_stripping_unaffected(self):
        self.assertEqual(normalize_token("monitoring"), normalize_token("monitor"))
        self.assertEqual(normalize_token("logging"), "log")


class EmbeddingSafetyNetTests(unittest.TestCase):
    # Regression coverage for the other half of the same live finding: even after the
    # stemming fix, a candidate can still fall just short of the combined-score ranking
    # floor. ranked_matches always admits the single best-by-embedding candidate as a
    # safety net, so the LLM verification step (the real semantic judgment layer) gets
    # a chance to weigh in rather than a purely heuristic combined score silently
    # excluding it.

    def test_best_embedding_candidate_is_rescued_below_the_combined_score_floor(self):
        # HIGH_KEYWORD's full keyword+phrase match drives the relative floor up to
        # ~0.64; BEST_EMBEDDING's own combined score (~0.56) falls below that floor
        # despite having the single highest embedding similarity of the two (~0.99
        # vs ~0.50) - without the safety net it would never reach LLM verification,
        # exactly the live failure this regression test is for.
        indexed = [
            {
                "id": "HIGH_KEYWORD",
                "text": "The system operating voltage shall be regulated within tolerance.",
                "source": None,
                "vector": [0.5, 0.87],
            },
            {
                "id": "BEST_EMBEDDING",
                "text": "The PBDU shall operate from a nominal 12 V vehicle electrical supply.",
                "source": None,
                "vector": [0.99, 0.14],
            },
        ]
        result = ranked_matches(indexed, [1.0, 0.0], query="operating voltage")
        by_id = {item["id"]: item for item in result["ranked"]}
        floor = max(0.30, result["best_score"] - 0.18)
        self.assertLess(by_id["BEST_EMBEDDING"]["score"], floor)
        ids = [item["id"] for item in result["matches"]]
        self.assertIn("BEST_EMBEDDING", ids)

    def test_safety_net_does_not_override_keyword_discrimination_when_embeddings_tie(self):
        # When embeddings can't meaningfully discriminate (all near-identical - the
        # realistic case for short technical phrases), keyword/parameter matching must
        # still be what determines relevance; the safety net should not blanket-admit
        # every near-tied candidate.
        indexed = [
            {"id": "RELEVANT", "text": "The system shall withstand a nominal current of 1A.", "source": None, "vector": [1.0, 0.0]},
            {"id": "IRRELEVANT", "text": "Each system requirement shall have a unique identifier.", "source": None, "vector": [0.99, 0.01]},
        ]
        result = ranked_matches(indexed, [1.0, 0.0], query="What nominal current shall the system withstand?")
        ids = [item["id"] for item in result["matches"]]
        self.assertNotIn("IRRELEVANT", ids)


if __name__ == "__main__":
    unittest.main()
