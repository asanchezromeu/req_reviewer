"""Reference knowledge base: chunking and ranking for ingested reference material.

SPEC.md Tier 2: standards excerpts, glossaries, project baselines - prose, not
"shall" statements, so requirement-tuned keyword/structural scoring doesn't
apply. Pure functions, no DB access (mirrors deterministic_review.py /
conflict_precheck.py); backend/retrieval.py owns storage and wires these in.
"""

import math
import re
from typing import Any, Dict, List

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def chunk_reference_text(text: str, max_chars: int = 800) -> List[str]:
    """Split reference text into paragraph-sized chunks, capped at `max_chars`.

    Paragraphs (blank-line separated) are kept whole when short enough;
    oversized paragraphs are further split at sentence boundaries and packed
    up to the limit.
    """
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(text.strip()) if p.strip()]
    chunks: List[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
            continue
        sentences = [s for s in _SENTENCE_SPLIT.split(paragraph) if s]
        current = ""
        for sentence in sentences:
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) > max_chars and current:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks


def _cosine_similarity(left: List[float], right: List[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def rank_reference_chunks(
    indexed_chunks: List[Dict[str, Any]],
    query_vector: List[float],
    min_similarity: float = 0.30,
) -> List[Dict[str, Any]]:
    """Rank indexed reference chunks by cosine similarity to `query_vector`.

    Each input chunk needs a `vector` key; output items are the input dicts
    with a `similarity` key added, filtered to `min_similarity` and sorted
    descending.
    """
    scored = []
    for chunk in indexed_chunks:
        similarity = _cosine_similarity(query_vector, chunk.get("vector", []))
        if similarity >= min_similarity:
            scored.append({**chunk, "similarity": similarity})
    scored.sort(key=lambda item: item["similarity"], reverse=True)
    return scored
