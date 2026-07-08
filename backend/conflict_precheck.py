"""Heuristic conflict pre-check.

Python port of the legacy Node prototype's ``src/conflict-precheck.mjs``. Flags
likely-conflicting requirement pairs by regex/keyword heuristics before an LLM
ever sees them, and reconciles an LLM review's conflict list against those
candidates so conflicts the model missed are not silently dropped.
"""

import re
from typing import Any, Dict, List, Optional, TypedDict


class Requirement(TypedDict):
    id: str
    text: str


class ConflictCandidate(TypedDict):
    id: str
    requirementIds: List[str]
    type: str
    severity: str
    evidence: str
    mitigation: str


NEGATIVE_PATTERNS = [
    re.compile(r"\bshall not\b", re.IGNORECASE),
    re.compile(r"\bmust not\b", re.IGNORECASE),
    re.compile(r"\bwill not\b", re.IGNORECASE),
    re.compile(r"\bnot\b", re.IGNORECASE),
    re.compile(r"\bnever\b", re.IGNORECASE),
    re.compile(r"\bprohibit", re.IGNORECASE),
    re.compile(r"\bdisable", re.IGNORECASE),
]

POSITIVE_PATTERNS = [
    re.compile(r"\bshall\b", re.IGNORECASE),
    re.compile(r"\bmust\b", re.IGNORECASE),
    re.compile(r"\bwill\b", re.IGNORECASE),
    re.compile(r"\btransmit\b", re.IGNORECASE),
    re.compile(r"\bprovide\b", re.IGNORECASE),
    re.compile(r"\benable\b", re.IGNORECASE),
    re.compile(r"\ballow\b", re.IGNORECASE),
]

STOPWORDS = {
    "the",
    "and",
    "shall",
    "must",
    "will",
    "when",
    "while",
    "with",
    "from",
    "between",
    "greater",
    "than",
    "less",
    "system",
    "data",
}

_NUMBER_PATTERN = re.compile(r"(.{0,25}?)(\d+(?:\.\d+)?)\s*(km/h|ms|s|v|a|%|lux|hz)?", re.IGNORECASE)
_MAX_CONTEXT = re.compile(r"within|less than|below|under|max|maximum|no more than", re.IGNORECASE)
_MIN_CONTEXT = re.compile(r"greater than|above|over|min|minimum|at least", re.IGNORECASE)


def find_conflict_candidates(requirements: List[Requirement]) -> List[ConflictCandidate]:
    candidates: List[ConflictCandidate] = []
    for left_index in range(len(requirements)):
        for right_index in range(left_index + 1, len(requirements)):
            left = requirements[left_index]
            right = requirements[right_index]
            similarity = _jaccard(_keywords(left["text"]), _keywords(right["text"]))
            left_negative = _matches_any(left["text"], NEGATIVE_PATTERNS)
            right_negative = _matches_any(right["text"], NEGATIVE_PATTERNS)
            left_positive = _matches_any(left["text"], POSITIVE_PATTERNS)
            right_positive = _matches_any(right["text"], POSITIVE_PATTERNS)

            if similarity >= 0.3 and left_negative != right_negative and left_positive and right_positive:
                candidates.append(
                    {
                        "id": f"PC-{len(candidates) + 1:03d}",
                        "requirementIds": [left["id"], right["id"]],
                        "type": "contradiction",
                        "severity": "high",
                        "evidence": (
                            f"{left['id']} requires the behavior while {right['id']} prohibits the "
                            "same or overlapping behavior."
                        ),
                        "mitigation": (
                            "Clarify the applicable mode, speed range, operating condition, or "
                            "product variant; otherwise choose one behavior and update the "
                            "conflicting requirement."
                        ),
                    }
                )
                continue

            threshold = _threshold_conflict(left, right, similarity)
            if threshold:
                candidates.append(
                    {
                        "id": f"PC-{len(candidates) + 1:03d}",
                        "requirementIds": [left["id"], right["id"]],
                        "type": "threshold mismatch",
                        "severity": "medium",
                        "evidence": threshold,
                        "mitigation": (
                            "Align the threshold values or split the requirements by interface, "
                            "mode, or variant."
                        ),
                    }
                )
    return candidates


def merge_candidate_conflicts(review: Dict[str, Any], candidates: List[ConflictCandidate]) -> Dict[str, Any]:
    existing_keys = {_conflict_key(conflict) for conflict in review["conflicts"]}
    missing = [candidate for candidate in candidates if _conflict_key(candidate) not in existing_keys]
    conflict_ids = {
        requirement_id
        for candidate in candidates
        for requirement_id in candidate["requirementIds"]
    }

    requirements = []
    for requirement in review["requirements"]:
        if requirement["id"] not in conflict_ids:
            requirements.append(requirement)
            continue

        score = 75 if requirement["score"] == 0 else min(requirement["score"], 82)
        flags = list(dict.fromkeys([*requirement.get("flags", []), "set-level conflict"]))
        requirements.append(
            {
                **requirement,
                "score": score,
                "flags": flags,
                "improvement": requirement.get("improvement")
                or "Resolve the set-level conflict by clarifying mode, condition, threshold, or selected behavior.",
            }
        )

    average_score = round(sum(r["score"] for r in requirements) / len(requirements)) if requirements else 0
    below_threshold_count = sum(1 for r in requirements if r["score"] < 85)

    return {
        **review,
        "summary": {
            **review["summary"],
            "averageScore": average_score,
            "belowThresholdCount": below_threshold_count,
            "conflictCount": len(review["conflicts"]) + len(missing),
        },
        "requirements": requirements,
        "conflicts": [*review["conflicts"], *missing],
    }


def _conflict_key(conflict: Dict[str, Any]) -> str:
    return "|".join(sorted(conflict["requirementIds"]))


def _threshold_conflict(left: Requirement, right: Requirement, similarity: float) -> str:
    if similarity < 0.35:
        return ""

    left_numbers = _numbers_with_units(left["text"])
    right_numbers = _numbers_with_units(right["text"])
    for left_number in left_numbers:
        for right_number in right_numbers:
            if left_number["unit"] != right_number["unit"] or left_number["operator"] != right_number["operator"]:
                continue
            delta = abs(left_number["value"] - right_number["value"])
            if delta > max(left_number["value"], right_number["value"]) * 0.25:
                return (
                    f"{left['id']} and {right['id']} use different {left_number['operator']} values: "
                    f"{left_number['value']}{left_number['unit']} vs {right_number['value']}{right_number['unit']}."
                )
    return ""


def _numbers_with_units(text: str) -> List[Dict[str, Any]]:
    results = []
    for match in _NUMBER_PATTERN.finditer(text):
        raw_unit = (match.group(3) or "").lower()
        results.append(
            {
                "value": _canonical_value(float(match.group(2)), raw_unit),
                "unit": _canonical_unit(raw_unit),
                "operator": _operator_from_context(match.group(1)),
            }
        )
    return results


def _canonical_value(value: float, unit: str) -> float:
    return value / 1000 if unit == "ms" else value


def _canonical_unit(unit: str) -> str:
    return "s" if unit == "ms" else unit


def _operator_from_context(context: str) -> str:
    if _MAX_CONTEXT.search(context):
        return "maximum"
    if _MIN_CONTEXT.search(context):
        return "minimum"
    return "value"


def _matches_any(text: str, patterns: List[re.Pattern]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _keywords(text: Optional[str]) -> set:
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    return {word for word in words if len(word) > 2 and word not in STOPWORDS}


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    return intersection / union
