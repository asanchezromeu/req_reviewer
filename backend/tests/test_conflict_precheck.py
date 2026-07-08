import unittest

try:
    from backend.conflict_precheck import find_conflict_candidates, merge_candidate_conflicts
except ImportError:
    from conflict_precheck import find_conflict_candidates, merge_candidate_conflicts


class ConflictPrecheckTests(unittest.TestCase):
    def test_precheck_catches_opposite_obligations_over_overlapping_behavior(self):
        requirements = [
            {
                "id": "REQ-1",
                "text": (
                    "The camera system shall transmit image data continuously when vehicle "
                    "speed is between 0 km/h and 200 km/h."
                ),
            },
            {
                "id": "REQ-2",
                "text": "The camera system shall not transmit image data when vehicle speed is greater than 120 km/h.",
            },
        ]

        candidates = find_conflict_candidates(requirements)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["type"], "contradiction")

    def test_candidate_conflicts_are_preserved_when_model_omits_them(self):
        review = {
            "summary": {"requirementCount": 2, "averageScore": 90, "belowThresholdCount": 0, "conflictCount": 0},
            "requirements": [
                {"id": "REQ-1", "score": 0, "flags": ["missing reviewer output"], "improvement": "Re-run."},
                {"id": "REQ-2", "score": 0, "flags": ["missing reviewer output"], "improvement": "Re-run."},
            ],
            "conflicts": [],
        }
        candidate = {
            "id": "PC-001",
            "requirementIds": ["REQ-1", "REQ-2"],
            "type": "contradiction",
            "severity": "high",
            "evidence": "Opposite obligations.",
            "mitigation": "Clarify mode.",
        }

        merged = merge_candidate_conflicts(review, [candidate])

        self.assertEqual(merged["summary"]["conflictCount"], 1)
        self.assertEqual(merged["summary"]["belowThresholdCount"], 2)
        self.assertEqual(merged["requirements"][0]["score"], 75)
        self.assertEqual(merged["conflicts"][0]["type"], "contradiction")


if __name__ == "__main__":
    unittest.main()
