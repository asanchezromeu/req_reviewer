"""Structured LLM I/O contract helpers.

Generalizes the legacy Node prototype's `review-contract.mjs` idea into a
schema-agnostic Python module: tolerant JSON extraction, plus id-anchored
reconciliation so a caller can guarantee "one result per expected id" even
when the model omits an id or returns something invalid for it. Malformed or
missing LLM output degrades to a caller-supplied deterministic fallback -
it must never be silently dropped or allowed to crash the caller.
"""

import json
from typing import Any, Callable, Dict, List


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
    end = t.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object in response")
    return json.loads(t[start : end + 1])


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
