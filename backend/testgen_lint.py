"""Deterministic anti-genericity lint pass (SPEC-ADDENDUM-A A.4, Verdict 2).

Pure functions, no LLM calls - checks already-generated test case content
against the anti-genericity bar from A.1, so it's testable via plain fixtures
per A.7 ("a lint pass over generated preconditions/criteria"). Reuses the
existing weak-wording/measurability heuristics from deterministic_review.py
rather than reinventing them.
"""

import re
from typing import Any, Dict, List

try:
    from .deterministic_review import WEAK_WORDS, _has_measure
except ImportError:
    from deterministic_review import WEAK_WORDS, _has_measure


BANNED_PHRASES = (
    "power on the device",
    "the output shall be correct",
)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*[a-zA-Z%]*")
_DIGIT_RE = re.compile(r"\d+(?:\.\d+)?")


def _generic_phrase_violations(lines: List[str], field_name: str) -> List[Dict[str, str]]:
    violations = []
    for line in lines:
        lower = line.lower()
        banned_hit = next((phrase for phrase in BANNED_PHRASES if phrase in lower), None)
        if banned_hit:
            violations.append(
                {
                    "item": f'{field_name}: "{line}"',
                    "why": "Generic placeholder phrasing, not project-specific.",
                }
            )
            continue
        weak_hits = [word for word in WEAK_WORDS if word in lower]
        if weak_hits:
            violations.append(
                {
                    "item": f'{field_name}: "{line}"',
                    "why": f"Vague wording ({', '.join(sorted(weak_hits))}) instead of a concrete value.",
                }
            )
    return violations


def _missing_measure_violations(criteria: List[str], require_quantity: bool) -> List[Dict[str, str]]:
    if not require_quantity:
        return []
    return [
        {
            "item": f'acceptance criterion: "{line}"',
            "why": "The requirement/context establishes a measurable quantity but this criterion "
            "doesn't state one.",
        }
        for line in criteria
        if not _has_measure(line)
    ]


def _traceable_digits(
    requirement_text: str, context_items: List[Dict[str, Any]], assumptions: List[Dict[str, Any]]
) -> set:
    # Compares on the bare numeral rather than the number+unit token, since a value can be
    # phrased with a different unit word in the source than in the generated text (e.g. an
    # assumption of "5 repetitions" grounding a generated "5 times") - the number itself is
    # what must be traceable, not its exact surrounding wording.
    pool_parts = [requirement_text]
    pool_parts += [str(item.get("value", "")) for item in context_items]
    pool_parts += [f"{a.get('text', '')} {a.get('value', '')}" for a in assumptions]
    pool = "\n".join(pool_parts)
    return {m.group(0) for m in _DIGIT_RE.finditer(pool)}


def _untraceable_value_violations(
    lines: List[str], field_name: str, traceable_digits: set
) -> List[Dict[str, str]]:
    violations = []
    for line in lines:
        for match in _NUMBER_RE.finditer(line):
            digit_match = _DIGIT_RE.match(match.group(0))
            digits = digit_match.group(0) if digit_match else match.group(0)
            if digits not in traceable_digits:
                violations.append(
                    {
                        "item": f'{field_name}: "{line}"',
                        "why": f'Value "{match.group(0).strip()}" is not traceable to the requirement, '
                        "project test context, or a marked assumption.",
                    }
                )
                break
    return violations


def check_anti_genericity(
    generated: Dict[str, Any],
    requirement_text: str,
    context_items: List[Dict[str, Any]],
    assumptions: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    verification_method = str(generated.get("verification_method", "test"))
    preconditions = [str(x) for x in generated.get("preconditions", []) or []]
    acceptance_criteria = [str(x) for x in generated.get("acceptance_criteria", []) or []]

    violations: List[Dict[str, str]] = []
    violations += _generic_phrase_violations(preconditions, "precondition")
    violations += _generic_phrase_violations(acceptance_criteria, "acceptance criterion")

    # Only force a measurable quantity when the requirement or context actually
    # establishes one - a purely functional/logging requirement with no inherent
    # quantity can be satisfied by an observable-state criterion instead (A.7).
    require_quantity = verification_method == "test" and (
        _has_measure(requirement_text) or any(_has_measure(str(item.get("value", ""))) for item in context_items)
    )
    violations += _missing_measure_violations(acceptance_criteria, require_quantity)

    traceable_digits = _traceable_digits(requirement_text, context_items, assumptions or [])
    violations += _untraceable_value_violations(preconditions, "precondition", traceable_digits)
    violations += _untraceable_value_violations(acceptance_criteria, "acceptance criterion", traceable_digits)

    return violations
