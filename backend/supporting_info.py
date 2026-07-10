"""Persistent, human-readable "supporting info" facts for test-case generation.

Distinct from `db.test_context_versions` (server.py) - that store lives in
`MemoryDatabase` by default and is wiped on every server restart. This is a
small flat JSON file on disk so confirmed domain-knowledge defaults (e.g. "all
electrical faults are simulated either with electronic loads or with fault
injection") survive restarts and are git-trackable project knowledge.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PATH = Path(__file__).parent / "data" / "supporting_info.json"


def load_supporting_info(path: Path = DEFAULT_PATH) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("facts", [])


def _save(facts: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"facts": facts}, indent=2), encoding="utf-8")


def append_supporting_fact(
    text: str,
    source: str,
    requirement_id: Optional[str] = None,
    path: Path = DEFAULT_PATH,
) -> Dict[str, Any]:
    facts = load_supporting_info(path)
    fact = {
        "id": str(uuid.uuid4()),
        "text": text,
        "source": source,
        "requirement_id": requirement_id,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    facts.append(fact)
    _save(facts, path)
    return fact


def remove_supporting_fact(fact_id: str, path: Path = DEFAULT_PATH) -> bool:
    facts = load_supporting_info(path)
    remaining = [f for f in facts if f["id"] != fact_id]
    if len(remaining) == len(facts):
        return False
    _save(remaining, path)
    return True


def format_supporting_info(facts: List[Dict[str, Any]]) -> str:
    if not facts:
        return "(no supporting info recorded yet)"
    return "\n".join(f"- {fact['text']}" for fact in facts)
