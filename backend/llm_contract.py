"""Structured LLM I/O contract helpers.

Generalizes the legacy Node prototype's `review-contract.mjs` idea into a
schema-agnostic Python module: tolerant JSON extraction, plus id-anchored
reconciliation so a caller can guarantee "one result per expected id" even
when the model omits an id or returns something invalid for it. Malformed or
missing LLM output degrades to a caller-supplied deterministic fallback -
it must never be silently dropped or allowed to crash the caller.
"""

import json
from typing import Any, Callable, Dict, List, Optional


def _repair_truncated_brackets(candidate: str) -> Optional[str]:
    """Close a JSON candidate that was cut off mid-structure (a model that stops
    generating - or gets stuck in a JSON-grammar-constrained decoding loop - partway
    through an array/object). Walks the string tracking open brackets outside of
    string literals, drops a dangling trailing comma, and appends the closers needed
    to balance what's still open. Returns None if nothing was actually unbalanced.
    """
    stack: List[str] = []
    in_string = False
    escape = False
    for ch in candidate:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    if not stack:
        return None
    closers = {"{": "}", "[": "]"}
    repaired = candidate.rstrip()
    if repaired.endswith(","):
        repaired = repaired[:-1]
    return repaired + "".join(closers[ch] for ch in reversed(stack))


def extract_json(text: str) -> Dict[str, Any]:
    """Extract a JSON object from LLM output, tolerant to markdown fences and surrounding prose."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if "\n" in t:
            first, rest = t.split("\n", 1)
            if first.lower().strip() in ("json", ""):
                t = rest
    start = t.find("{")
    if start == -1:
        raise ValueError("No JSON object in response")
    end = t.rfind("}")
    # No closing brace at all is the most extreme case of the same truncation this
    # is meant to repair - take everything from the opening brace to the end of the
    # response rather than giving up immediately.
    candidate = t[start : end + 1] if end != -1 and end >= start else t[start:]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        repaired = _repair_truncated_brackets(candidate)
        if repaired is None:
            raise
        return json.loads(repaired)


def reconcile_by_id(
    expected_ids: List[str],
    parsed_items: List[Dict[str, Any]],
    id_key: str,
    make_fallback: Callable[[str], Dict[str, Any]],
    is_valid: Callable[[Dict[str, Any]], bool] = lambda item: True,
) -> List[Dict[str, Any]]:
    """Return exactly one item per id in `expected_ids`, in that order.

    Uses the LLM-parsed item for an id when present and `is_valid`; otherwise
    calls `make_fallback(id)` to synthesize a deterministic placeholder. IDs
    are matched case-insensitively after stripping whitespace; the first
    parsed item wins if the model returned duplicates for the same id.
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in parsed_items:
        raw_id = item.get(id_key)
        if raw_id is None:
            continue
        key = str(raw_id).strip().upper()
        if key and key not in by_id:
            by_id[key] = item

    reconciled = []
    for expected_id in expected_ids:
        key = str(expected_id).strip().upper()
        item = by_id.get(key)
        if item is not None and is_valid(item):
            reconciled.append(item)
        else:
            reconciled.append(make_fallback(expected_id))
    return reconciled
