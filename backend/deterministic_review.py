"""Deterministic (non-LLM) requirement parsing and scoring.

Ported from the legacy Streamlit prototype's ``req_analysis.py``. This is the
baseline scorer/parser: no LLM calls, pure rules. It serves as the non-LLM
fallback and regression oracle called out in SPEC.md section 6 — wiring it up
as a fallback path in the review endpoints is future work, not done here.
"""

import csv
import io
import json
import re
from dataclasses import asdict, dataclass
from itertools import combinations
from typing import Any, Iterable


REQUIREMENT_COLUMNS = ("requirement", "text", "description", "statement")
ID_COLUMNS = ("id", "req_id", "requirement_id", "key")

WEAK_WORDS = {
    "adequate",
    "appropriate",
    "as needed",
    "as possible",
    "fast",
    "high",
    "low",
    "many",
    "optimize",
    "proper",
    "rapid",
    "robust",
    "seamless",
    "sufficient",
    "support",
    "timely",
    "user-friendly",
}

NEGATION_PATTERNS = (
    r"\bshall not\b",
    r"\bmust not\b",
    r"\bwill not\b",
    r"\bnever\b",
    r"\bprohibit(?:s|ed)?\b",
    r"\bdisable(?:s|d)?\b",
)

POSITIVE_PATTERNS = (
    r"\bshall\b",
    r"\bmust\b",
    r"\bwill\b",
    r"\benable(?:s|d)?\b",
    r"\ballow(?:s|ed)?\b",
    r"\bprovide(?:s|d)?\b",
)


@dataclass
class Requirement:
    id: str
    text: str
    source: str = ""
    parents: str = ""


@dataclass
class RequirementReview:
    id: str
    requirement: str
    score: int
    needs_improvement: bool
    improvement: str
    flags: str


@dataclass
class Conflict:
    requirements: str
    type: str
    evidence: str
    mitigation: str


def parse_requirements(filename: str, raw_bytes: bytes) -> list[Requirement]:
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    text = raw_bytes.decode("utf-8-sig")

    if extension == "csv":
        return _parse_csv(text)
    if extension == "json":
        return _parse_json(text)

    raise ValueError("Upload a .csv or .json file.")


def _parse_csv(text: str) -> list[Requirement]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV file has no header row.")

    field_map = {name.lower().strip(): name for name in reader.fieldnames}
    text_column = _first_existing(field_map, REQUIREMENT_COLUMNS)
    id_column = _first_existing(field_map, ID_COLUMNS)
    if not text_column:
        raise ValueError(
            "CSV must contain a requirement text column, for example 'requirement' or 'text'."
        )

    requirements = []
    for index, row in enumerate(reader, start=1):
        req_text = (row.get(text_column) or "").strip()
        if not req_text:
            continue
        req_id = (row.get(id_column) or f"REQ-{index:03d}").strip() if id_column else f"REQ-{index:03d}"
        source_column = field_map.get("source")
        parents_column = field_map.get("parents")
        source = (row.get(source_column) or "").strip() if source_column else ""
        parents = (row.get(parents_column) or "").strip() if parents_column else ""
        requirements.append(Requirement(id=req_id, text=req_text, source=source, parents=parents))
    return _dedupe_ids(requirements)


def _parse_json(text: str) -> list[Requirement]:
    payload = json.loads(text)
    items: Iterable[Any]
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("requirements"), list):
        items = payload["requirements"]
    else:
        raise ValueError("JSON must be a list or an object with a 'requirements' list.")

    requirements = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, str):
            requirements.append(Requirement(id=f"REQ-{index:03d}", text=item.strip()))
            continue
        if not isinstance(item, dict):
            continue
        lowered = {str(key).lower().strip(): key for key in item}
        text_key = _first_existing(lowered, REQUIREMENT_COLUMNS)
        id_key = _first_existing(lowered, ID_COLUMNS)
        req_text = str(item.get(text_key, "")).strip() if text_key else ""
        if not req_text:
            continue
        req_id = str(item.get(id_key, f"REQ-{index:03d}")).strip() if id_key else f"REQ-{index:03d}"
        source_key = lowered.get("source")
        parents_key = lowered.get("parents")
        source = str(item.get(source_key, "")).strip() if source_key else ""
        parents = item.get(parents_key, "") if parents_key else ""
        if isinstance(parents, list):
            parents = ", ".join(str(parent) for parent in parents)
        parents = str(parents).strip()
        requirements.append(Requirement(id=req_id, text=req_text, source=source, parents=parents))
    return _dedupe_ids(requirements)


def review_requirements(requirements: list[Requirement]) -> tuple[list[RequirementReview], list[Conflict]]:
    reviews = [review_requirement(requirement) for requirement in requirements]
    conflicts = detect_conflicts(requirements)
    return reviews, conflicts


def review_requirement(requirement: Requirement) -> RequirementReview:
    text = requirement.text.strip()
    lower = text.lower()
    flags: list[str] = []
    score = 100

    if not re.search(r"\bshall\b|\bmust\b", lower):
        score -= 12
        flags.append("Use shall/must")
    if any(word in lower for word in WEAK_WORDS):
        score -= 10
        flags.append("Ambiguous wording")
    if not _has_measure(text):
        score -= 12
        flags.append("No measurable criterion")
    if _may_not_be_singular(text):
        score -= 8
        flags.append("May not be singular")
    if not re.search(r"\bwhen\b|\bwhile\b|\bwithin\b|\bbetween\b|\bfrom\b|\bat\b|\bif\b", lower):
        score -= 8
        flags.append("Context missing")
    if len(text.split()) < 7:
        score -= 8
        flags.append("Too short")
    if len(text.split()) > 45:
        score -= 6
        flags.append("Too long")
    if re.search(r"\b(tbd|todo|n/a|unknown)\b", lower):
        score -= 18
        flags.append("Incomplete placeholder")
    if not requirement.source and not requirement.parents:
        score -= 8
        flags.append("Traceability missing")

    score = max(0, min(100, score))
    needs_improvement = score < 85
    improvement = _proposal(text, flags) if needs_improvement else ""

    return RequirementReview(
        id=requirement.id,
        requirement=text,
        score=score,
        needs_improvement=needs_improvement,
        improvement=improvement,
        flags=", ".join(flags) if flags else "OK",
    )


def detect_conflicts(requirements: list[Requirement]) -> list[Conflict]:
    conflicts: list[Conflict] = []
    for left, right in combinations(requirements, 2):
        left_norm = _normalize(left.text)
        right_norm = _normalize(right.text)
        similarity = _jaccard(_keywords(left_norm), _keywords(right_norm))

        if left_norm == right_norm and left.id != right.id:
            conflicts.append(
                Conflict(
                    requirements=f"{left.id}, {right.id}",
                    type="Duplicate",
                    evidence="Requirements are textually identical after normalization.",
                    mitigation="Keep one requirement and merge trace links or rationale into the retained item.",
                )
            )
            continue

        if similarity < 0.38:
            continue

        left_negative = _matches_any(left_norm, NEGATION_PATTERNS)
        right_negative = _matches_any(right_norm, NEGATION_PATTERNS)
        left_positive = _matches_any(left_norm, POSITIVE_PATTERNS)
        right_positive = _matches_any(right_norm, POSITIVE_PATTERNS)
        if left_negative != right_negative and left_positive and right_positive:
            conflicts.append(
                Conflict(
                    requirements=f"{left.id}, {right.id}",
                    type="Contradictory behavior",
                    evidence="Similar subject with opposite permission/prohibition wording.",
                    mitigation="Clarify the operating state or variant where each behavior applies, or choose one behavior.",
                )
            )
            continue

        numeric_conflict = _numeric_conflict(left_norm, right_norm)
        if numeric_conflict:
            conflicts.append(
                Conflict(
                    requirements=f"{left.id}, {right.id}",
                    type="Inconsistent threshold",
                    evidence=numeric_conflict,
                    mitigation="Align the threshold or split the requirements by mode, environment, or product variant.",
                )
            )

    return conflicts


def to_dicts(items: Iterable[Any]) -> list[dict[str, Any]]:
    return [asdict(item) for item in items]


def _first_existing(field_map: dict[str, str], candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if candidate in field_map:
            return field_map[candidate]
    return None


def _dedupe_ids(requirements: list[Requirement]) -> list[Requirement]:
    seen: dict[str, int] = {}
    result = []
    for requirement in requirements:
        base = requirement.id or "REQ"
        count = seen.get(base, 0)
        seen[base] = count + 1
        req_id = base if count == 0 else f"{base}-{count + 1}"
        result.append(
            Requirement(
                id=req_id,
                text=requirement.text,
                source=requirement.source,
                parents=requirement.parents,
            )
        )
    return result


def _has_measure(text: str) -> bool:
    return bool(
        re.search(r"\d", text)
        or re.search(r"\bwithin\b|\bat least\b|\bno more than\b|\bbetween\b|\bfrom\b|\bper\b", text, re.I)
    )


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"[.!?]+", text) if part.strip()]


def _may_not_be_singular(text: str) -> bool:
    lower = text.lower()
    if len(_sentences(text)) > 1:
        return True
    if len(re.findall(r"\bshall\b|\bmust\b", lower)) > 1:
        return True
    if not re.search(r"\b(and|or)\b", lower):
        return False
    range_phrases = (
        r"\bbetween\b.+\band\b",
        r"\bfrom\b.+\bto\b",
        r"\+/-",
        r"\bwith a tolerance\b",
    )
    return not any(re.search(pattern, lower) for pattern in range_phrases)


def _proposal(text: str, flags: list[str]) -> str:
    actions = []
    if "Use shall/must" in flags:
        actions.append("rewrite with 'shall'")
    if "Ambiguous wording" in flags:
        actions.append("replace vague terms with measurable limits")
    if "No measurable criterion" in flags:
        actions.append("add measurable threshold, unit, timing, or pass/fail criterion")
    if "May not be singular" in flags:
        actions.append("split into one requirement per behavior")
    if "Context missing" in flags:
        actions.append("state condition, mode, interface, or operating range")
    if "Incomplete placeholder" in flags:
        actions.append("resolve placeholder content")
    if "Traceability missing" in flags:
        actions.append("add source or parent link")
    if not actions:
        actions.append("tighten wording for clarity and verifiability")
    return "; ".join(actions[:3]).capitalize() + "."


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9.%<>= -]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _keywords(text: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "be",
        "by",
        "for",
        "from",
        "in",
        "its",
        "of",
        "or",
        "shall",
        "should",
        "system",
        "the",
        "to",
        "when",
        "with",
    }
    return {word for word in re.findall(r"[a-z0-9]+", text) if len(word) > 2 and word not in stopwords}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _numeric_conflict(left: str, right: str) -> str:
    left_numbers = _numbers_with_context(left)
    right_numbers = _numbers_with_context(right)
    for left_value, left_unit, left_op in left_numbers:
        for right_value, right_unit, right_op in right_numbers:
            left_value, left_unit = _canonical_number(left_value, left_unit)
            right_value, right_unit = _canonical_number(right_value, right_unit)
            if left_unit != right_unit:
                continue
            if left_op == "max" and right_op == "min" and left_value < right_value:
                return f"Maximum {left_value:g}{left_unit} is below minimum {right_value:g}{right_unit}."
            if left_op == "min" and right_op == "max" and left_value > right_value:
                return f"Minimum {left_value:g}{left_unit} is above maximum {right_value:g}{right_unit}."
            if left_op == right_op and abs(left_value - right_value) > max(left_value, right_value) * 0.25:
                return f"Similar requirement uses different {left_op} values: {left_value:g}{left_unit} vs {right_value:g}{right_unit}."
    return ""


def _numbers_with_context(text: str) -> list[tuple[float, str, str]]:
    results = []
    pattern = re.compile(
        r"(?P<context>.{0,25}?)(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>km/h|ms|s|v|a|c|%|kbps|mbps|lux|hz)?"
    )
    for match in pattern.finditer(text):
        context = match.group("context")
        value = float(match.group("value"))
        unit = match.group("unit") or ""
        operator = "exact"
        if re.search(r"less than|below|under|maximum|max|no more than|within|<=", context):
            operator = "max"
        elif re.search(r"greater than|above|over|minimum|min|at least|>=", context):
            operator = "min"
        results.append((value, unit, operator))
    return results


def _canonical_number(value: float, unit: str) -> tuple[float, str]:
    if unit == "ms":
        return value / 1000, "s"
    return value, unit
