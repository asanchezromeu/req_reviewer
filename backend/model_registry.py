"""Model registry + dataset-export helpers for SPEC.md Tier 3 scaffolding.

Pure functions, no DB access (mirrors reference_kb.py / deterministic_review.py):
backend/server.py owns storage (db.model_registry, db.training_examples) and
wires these in.
"""

import json
import os
from typing import Any, Dict, Optional

FEATURES = ("review", "search", "summary", "classify", "ask")


def default_entry() -> Dict[str, str]:
    return {"provider": "ollama", "model": os.environ.get("OLLAMA_MODEL", "gemma3:1b")}


def score_matches_expectation(label: str, score: int, threshold: int = 85) -> bool:
    """Does a review score land on the side of the threshold its corpus label implies?"""
    if label == "good":
        return score >= threshold
    if label == "bad":
        return score < threshold
    return True


def build_training_row(example: Dict[str, Any], system_prompt: str) -> Dict[str, Any]:
    """One JSONL chat-format row for a corpus example, matching analyze_one's I/O shape.

    The target score/proposed_fix are not invented: they come from running the
    deterministic scorer (backend/deterministic_review.py) on the corrected
    text (or the original, for "good" examples with no correction), so every
    exported label is grounded in the same heuristics used elsewhere as a
    fallback/oracle rather than a hand-picked number.
    """
    try:
        from . import deterministic_review
    except ImportError:
        import deterministic_review

    req_id = f"EX-{example['id'][:8]}"
    req_text = example["requirement_text"]
    target_text = example.get("corrected_text") or req_text

    review = deterministic_review.review_requirement(
        deterministic_review.Requirement(id=req_id, text=target_text)
    )
    assistant_content = {
        "overall_score": review.score,
        "summary": example.get("explanation")
        or ("Well-formed requirement." if example.get("label") == "good" else "Needs improvement."),
        "proposed_fix": target_text if target_text != req_text else "",
        "rules": {},
    }
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f'Requirement ID: {req_id}\nRequirement text: "{req_text}"'},
            {"role": "assistant", "content": json.dumps(assistant_content)},
        ]
    }
