import asyncio
import unittest
from unittest.mock import patch

try:
    from backend import server
except ImportError:
    import server


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class ReviewFallbackTests(unittest.TestCase):
    def test_malformed_llm_json_degrades_to_deterministic_fallback(self):
        async def fake_llm_complete(*args, **kwargs):
            return "not valid json at all"

        with patch.object(server, "llm_complete", fake_llm_complete):
            result = run(
                server.analyze_one(
                    "ollama",
                    "gemma3:1b",
                    "REQ-1",
                    "The system shall be fast and easy to use.",
                )
            )

        self.assertTrue(result["fallback"])
        self.assertIn("error", result)
        # Deterministic scorer should flag ambiguous wording/no measurable
        # criterion for this text, so the score must not be a bare zero.
        self.assertGreater(result["overall_score"], 0)
        self.assertEqual(set(result["rules"].keys()), {rule["key"] for rule in server.INCOSE_RULES})

    def test_llm_complete_raising_also_degrades_gracefully(self):
        async def fake_llm_complete(*args, **kwargs):
            raise RuntimeError("provider unreachable")

        with patch.object(server, "llm_complete", fake_llm_complete):
            result = run(
                server.analyze_one(
                    "ollama",
                    "gemma3:1b",
                    "REQ-2",
                    "The zone controller shall respond to a diagnostic request within 200 ms under nominal load.",
                    ollama_url="http://localhost:11434",
                )
            )

        self.assertTrue(result["fallback"])
        self.assertIn("provider unreachable", result["summary"])
        # This requirement text is actually well-formed, so the heuristic
        # fallback score should be respectably high, not zero.
        self.assertGreaterEqual(result["overall_score"], 70)

    def test_well_formed_llm_json_is_used_as_is(self):
        async def fake_llm_complete(*args, **kwargs):
            return '{"overall_score": 91, "summary": "Good.", "proposed_fix": "", "rules": {}}'

        with patch.object(server, "llm_complete", fake_llm_complete):
            result = run(
                server.analyze_one("ollama", "gemma3:1b", "REQ-3", "The system shall log events.")
            )

        self.assertNotIn("fallback", result)
        self.assertEqual(result["overall_score"], 91)


if __name__ == "__main__":
    unittest.main()
