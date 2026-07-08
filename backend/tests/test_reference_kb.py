import unittest

try:
    from backend.reference_kb import chunk_reference_text, rank_reference_chunks
except ImportError:
    from reference_kb import chunk_reference_text, rank_reference_chunks


class ChunkReferenceTextTests(unittest.TestCase):
    def test_splits_on_paragraph_boundaries(self):
        text = "First paragraph about voltage tolerances.\n\nSecond paragraph about timing."
        chunks = chunk_reference_text(text)
        self.assertEqual(chunks, [
            "First paragraph about voltage tolerances.",
            "Second paragraph about timing.",
        ])

    def test_short_single_paragraph_returns_one_chunk(self):
        self.assertEqual(chunk_reference_text("Just one short paragraph."), ["Just one short paragraph."])

    def test_oversized_paragraph_is_windowed_by_sentence(self):
        sentence = "This is a moderately long sentence about electrical tolerances. "
        paragraph = sentence * 20  # ~1300 chars, well over max_chars=100
        chunks = chunk_reference_text(paragraph, max_chars=100)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 120)  # small slack for the last packed sentence

    def test_empty_text_returns_no_chunks(self):
        self.assertEqual(chunk_reference_text("   \n\n  "), [])


class RankReferenceChunksTests(unittest.TestCase):
    def test_ranks_by_similarity_descending(self):
        indexed = [
            {"id": "REF-1", "text": "low similarity", "vector": [0.0, 1.0]},
            {"id": "REF-2", "text": "high similarity", "vector": [1.0, 0.0]},
        ]
        ranked = rank_reference_chunks(indexed, [1.0, 0.0], min_similarity=0.0)
        self.assertEqual([item["id"] for item in ranked], ["REF-2", "REF-1"])

    def test_filters_below_threshold(self):
        indexed = [
            {"id": "REF-1", "text": "orthogonal", "vector": [0.0, 1.0]},
            {"id": "REF-2", "text": "aligned", "vector": [1.0, 0.0]},
        ]
        ranked = rank_reference_chunks(indexed, [1.0, 0.0], min_similarity=0.5)
        self.assertEqual([item["id"] for item in ranked], ["REF-2"])

    def test_empty_indexed_chunks_returns_empty_list(self):
        self.assertEqual(rank_reference_chunks([], [1.0, 0.0]), [])


if __name__ == "__main__":
    unittest.main()
