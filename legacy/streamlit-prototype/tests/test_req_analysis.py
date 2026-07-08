import unittest
from pathlib import Path

from req_analysis import Requirement, parse_requirements, review_requirement, review_requirements
from main import _merge_reviews
from req_analysis import RequirementReview


ROOT = Path(__file__).resolve().parents[1]


class RequirementAnalysisTests(unittest.TestCase):
    def test_csv_sample_parses_traceability(self):
        requirements = parse_requirements(
            "requirements_sample.csv",
            (ROOT / "samples" / "requirements_sample.csv").read_bytes(),
        )

        self.assertEqual(len(requirements), 12)
        self.assertEqual(requirements[0].id, "REQ-CAM-001")
        self.assertEqual(requirements[0].source, "SN-CAM-01")

    def test_weak_requirement_gets_compact_proposal(self):
        review = review_requirement(
            Requirement("REQ-1", "The camera shall start quickly.", source="SN-1")
        )

        self.assertLess(review.score, 85)
        self.assertTrue(review.needs_improvement)
        self.assertIn("measurable", review.improvement.lower())

    def test_detects_behavior_and_threshold_conflicts(self):
        requirements = [
            Requirement(
                "REQ-CAM-004",
                "The camera system shall provide a valid image stream to the ADAS ECU within 500 ms after ignition-on at supply voltage from 9 V to 16 V.",
                "SN-CAM-04",
            ),
            Requirement(
                "REQ-CAM-005",
                "The camera system shall provide the first valid image frame to the ADAS ECU within 2 s after ignition-on.",
                "SN-CAM-04",
            ),
            Requirement(
                "REQ-CAM-006",
                "The camera shall not transmit image data when the vehicle speed is greater than 120 km/h.",
                "SN-CAM-05",
            ),
            Requirement(
                "REQ-CAM-007",
                "The camera system shall transmit image data continuously while the vehicle speed is between 0 km/h and 200 km/h.",
                "SN-CAM-05",
            ),
        ]

        _, conflicts = review_requirements(requirements)
        conflict_types = {conflict.type for conflict in conflicts}

        self.assertIn("Inconsistent threshold", conflict_types)
        self.assertIn("Contradictory behavior", conflict_types)

    def test_json_traceability_keys_are_case_insensitive(self):
        raw = b'{"requirements": [{"ID": "REQ-1", "Source": "SN-1", "Parents": ["SN-1"], "Text": "The controller shall report voltage within 10 ms when requested."}]}'

        requirements = parse_requirements("input.json", raw)

        self.assertEqual(requirements[0].source, "SN-1")
        self.assertEqual(requirements[0].parents, "SN-1")

    def test_range_wording_is_not_flagged_as_non_singular(self):
        review = review_requirement(
            Requirement(
                "REQ-1",
                "The camera shall detect lane markings when illumination is between 1 lux and 100000 lux.",
                source="SN-1",
            )
        )

        self.assertNotIn("May not be singular", review.flags)

    def test_partial_llm_reviews_do_not_drop_baseline_requirements(self):
        baseline = [
            RequirementReview("REQ-1", "A", 90, False, "", "OK"),
            RequirementReview("REQ-2", "B", 70, True, "Fix.", "No measurable criterion"),
        ]
        partial_llm = [RequirementReview("REQ-1", "A", 95, False, "", "OK")]

        merged = _merge_reviews(baseline, partial_llm)

        self.assertEqual([review.id for review in merged], ["REQ-1", "REQ-2"])
        self.assertEqual(merged[1].score, 70)


if __name__ == "__main__":
    unittest.main()
