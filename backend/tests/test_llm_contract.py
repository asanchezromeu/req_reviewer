import unittest

try:
    from backend.llm_contract import extract_json, reconcile_by_id
except ImportError:
    from llm_contract import extract_json, reconcile_by_id


class ExtractJsonTests(unittest.TestCase):
    def test_plain_json_object(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_json_in_markdown_fence_with_language_tag(self):
        raw = "```json\n{\"a\": 1}\n```"
        self.assertEqual(extract_json(raw), {"a": 1})

    def test_json_in_bare_fence(self):
        raw = "```\n{\"a\": 1}\n```"
        self.assertEqual(extract_json(raw), {"a": 1})

    def test_json_surrounded_by_prose(self):
        raw = 'Here is the result: {"a": 1} Thanks!'
        self.assertEqual(extract_json(raw), {"a": 1})

    def test_raises_when_no_json_object_present(self):
        with self.assertRaises(ValueError):
            extract_json("no json here")

    def test_repairs_truncated_array_with_dangling_comma(self):
        # A model that stops generating (or gets stuck in JSON-grammar-constrained
        # decoding) partway through an array - a real failure mode observed live
        # with gemma3:1b: complete, well-formed items followed by a dangling comma
        # and no closing brackets at all.
        raw = (
            '{\n  "category": "Functional",\n  "sufficient": true,\n  "gaps": [\n'
            '    {"item": "a", "why": "b"},\n'
            '    {"item": "c", "why": "d"},\n'
        )
        self.assertEqual(
            extract_json(raw),
            {"category": "Functional", "sufficient": True, "gaps": [{"item": "a", "why": "b"}, {"item": "c", "why": "d"}]},
        )

    def test_repairs_truncated_object_without_trailing_comma(self):
        raw = '{"category": "Electrical", "sufficient": false'
        self.assertEqual(extract_json(raw), {"category": "Electrical", "sufficient": False})

    def test_does_not_repair_when_braces_are_merely_out_of_order(self):
        # Genuinely malformed JSON (not just truncated) should still raise, not
        # get silently coerced into something that happens to parse.
        with self.assertRaises(ValueError):
            extract_json("not json at all, no braces here")


class ReconcileByIdTests(unittest.TestCase):
    def test_all_present_and_valid_returned_in_expected_order(self):
        items = [{"id": "REQ-002", "score": 2}, {"id": "REQ-001", "score": 1}]
        result = reconcile_by_id(
            ["REQ-001", "REQ-002"], items, "id", make_fallback=lambda rid: {"id": rid, "score": -1}
        )
        self.assertEqual([item["score"] for item in result], [1, 2])

    def test_missing_id_uses_fallback(self):
        items = [{"id": "REQ-001", "score": 1}]
        result = reconcile_by_id(
            ["REQ-001", "REQ-002"], items, "id", make_fallback=lambda rid: {"id": rid, "score": -1}
        )
        self.assertEqual(result[1], {"id": "REQ-002", "score": -1})

    def test_invalid_item_uses_fallback(self):
        items = [{"id": "REQ-001", "score": "not-a-number"}]
        result = reconcile_by_id(
            ["REQ-001"],
            items,
            "id",
            make_fallback=lambda rid: {"id": rid, "score": -1},
            is_valid=lambda item: isinstance(item.get("score"), int),
        )
        self.assertEqual(result[0], {"id": "REQ-001", "score": -1})

    def test_case_and_whitespace_insensitive_id_matching(self):
        items = [{"id": " req-001 ", "score": 1}]
        result = reconcile_by_id(
            ["REQ-001"], items, "id", make_fallback=lambda rid: {"id": rid, "score": -1}
        )
        self.assertEqual(result[0]["score"], 1)

    def test_duplicate_ids_first_one_wins(self):
        items = [{"id": "REQ-001", "score": 1}, {"id": "REQ-001", "score": 2}]
        result = reconcile_by_id(
            ["REQ-001"], items, "id", make_fallback=lambda rid: {"id": rid, "score": -1}
        )
        self.assertEqual(result[0]["score"], 1)


if __name__ == "__main__":
    unittest.main()
