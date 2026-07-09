"""Core retrieval module: the canonical requirement store, embedding index, and search/summary logic.

Renamed and unified from the legacy "showcase" module (see SPEC.md Phase 1 — API consolidation).
This is now THE requirements store for the whole engine: single collection, no "sets" concept.
"""

import asyncio
import csv
import io
import json
import math
import os
import re
import sqlite3
import threading
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import requests
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

try:
    from .llm_contract import extract_json, reconcile_by_id
    from .search_prompts import SEARCH_VERIFICATION_PROMPT
    from .summary_prompts import EXECUTIVE_SUMMARY_PROMPT
    from .reference_kb import chunk_reference_text, rank_reference_chunks
except ImportError:
    from llm_contract import extract_json, reconcile_by_id
    from search_prompts import SEARCH_VERIFICATION_PROMPT
    from summary_prompts import EXECUTIVE_SUMMARY_PROMPT
    from reference_kb import chunk_reference_text, rank_reference_chunks


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Requirement(BaseModel):
    id: str = Field(default_factory=lambda: f"REQ-{uuid.uuid4().hex[:8].upper()}")
    text: str
    source: Optional[str] = None


class SaveRequirementsBody(BaseModel):
    requirements: List[Requirement]
    embedding_model: str = "embeddinggemma"
    ollama_url: str = "http://localhost:11434"


class IndexRebuildBody(BaseModel):
    embedding_model: str = "embeddinggemma"
    ollama_url: str = "http://localhost:11434"


class SearchBody(BaseModel):
    query: str
    embedding_model: str = "embeddinggemma"
    ollama_url: str = "http://localhost:11434"
    min_similarity: float = 0.30
    provider: str = "ollama"
    llm_model: str = "gemma3:1b"


class SummaryBody(BaseModel):
    query: str
    embedding_model: str = "embeddinggemma"
    llm_model: str = "gemma3:1b"
    ollama_url: str = "http://localhost:11434"
    min_similarity: float = 0.30
    summary_top_k: int = 3


class ReferenceIngestBody(BaseModel):
    document: str
    title: Optional[str] = None
    text: str
    embedding_model: str = "embeddinggemma"
    ollama_url: str = "http://localhost:11434"


class RequirementStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS requirements (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    source TEXT,
                    revision INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS requirement_embeddings (
                    requirement_id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    vector TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(requirement_id)
                        REFERENCES requirements(id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS reference_chunks (
                    id TEXT PRIMARY KEY,
                    document TEXT NOT NULL,
                    title TEXT,
                    text TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reference_embeddings (
                    chunk_id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    vector TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(chunk_id)
                        REFERENCES reference_chunks(id)
                        ON DELETE CASCADE
                );
                """
            )

    def list_requirements(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, text, source, revision, updated_at
                FROM requirements
                ORDER BY id COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_requirements(self, requirements: List[Requirement]) -> List[Dict[str, Any]]:
        normalized = []
        seen = set()
        for item in requirements:
            req_id = item.id.strip()
            text = item.text.strip()
            if not req_id or not text:
                continue
            if req_id in seen:
                raise ValueError(f"Duplicate requirement ID: {req_id}")
            seen.add(req_id)
            normalized.append((req_id, text, (item.source or "").strip() or None))

        now = utc_now()
        with self.connect() as connection:
            existing = {
                row["id"]: dict(row)
                for row in connection.execute(
                    "SELECT id, text, source, revision FROM requirements"
                ).fetchall()
            }
            if normalized:
                placeholders = ",".join("?" for _ in normalized)
                connection.execute(
                    f"DELETE FROM requirements WHERE id NOT IN ({placeholders})",
                    [row[0] for row in normalized],
                )
            else:
                connection.execute("DELETE FROM requirements")

            for req_id, text, source in normalized:
                previous = existing.get(req_id)
                if previous is None:
                    revision = 1
                elif previous["text"] == text and previous["source"] == source:
                    revision = previous["revision"]
                else:
                    revision = previous["revision"] + 1
                connection.execute(
                    """
                    INSERT INTO requirements(id, text, source, revision, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        text = excluded.text,
                        source = excluded.source,
                        revision = excluded.revision,
                        updated_at = excluded.updated_at
                    """,
                    (req_id, text, source, revision, now),
                )
        return self.list_requirements()

    def stale_requirements(self, model: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.id, r.text, r.source, r.revision
                FROM requirements r
                LEFT JOIN requirement_embeddings e ON e.requirement_id = r.id
                WHERE e.requirement_id IS NULL
                   OR e.model != ?
                   OR e.revision != r.revision
                ORDER BY r.id COLLATE NOCASE
                """,
                (model,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_embeddings(
        self,
        model: str,
        requirements: List[Dict[str, Any]],
        vectors: List[List[float]],
    ) -> None:
        if len(requirements) != len(vectors):
            raise ValueError("Ollama returned an unexpected number of embeddings")
        now = utc_now()
        with self.connect() as connection:
            for requirement, vector in zip(requirements, vectors):
                current = connection.execute(
                    "SELECT revision FROM requirements WHERE id = ?",
                    (requirement["id"],),
                ).fetchone()
                if current is None or current["revision"] != requirement["revision"]:
                    continue
                connection.execute(
                    """
                    INSERT INTO requirement_embeddings(
                        requirement_id, model, revision, vector, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(requirement_id) DO UPDATE SET
                        model = excluded.model,
                        revision = excluded.revision,
                        vector = excluded.vector,
                        updated_at = excluded.updated_at
                    """,
                    (
                        requirement["id"],
                        model,
                        requirement["revision"],
                        json.dumps(vector),
                        now,
                    ),
                )

    def indexed_requirements(self, model: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.id, r.text, r.source, e.vector
                FROM requirements r
                JOIN requirement_embeddings e ON e.requirement_id = r.id
                WHERE e.model = ? AND e.revision = r.revision
                """,
                (model,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "text": row["text"],
                "source": row["source"],
                "vector": json.loads(row["vector"]),
            }
            for row in rows
        ]

    def counts(self, model: str) -> Dict[str, int]:
        with self.connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM requirements"
            ).fetchone()[0]
            indexed = connection.execute(
                """
                SELECT COUNT(*)
                FROM requirements r
                JOIN requirement_embeddings e ON e.requirement_id = r.id
                WHERE e.model = ? AND e.revision = r.revision
                """,
                (model,),
            ).fetchone()[0]
        return {"total": total, "indexed": indexed, "pending": total - indexed}

    def add_reference_chunks(
        self, document: str, title: Optional[str], chunks: List[str]
    ) -> List[Dict[str, Any]]:
        """Append chunks for one ingested document (additive - does not remove existing chunks).

        BEGIN IMMEDIATE acquires the write lock before the count read, so two
        concurrent ingests of the same document can't both read the same
        existing_count and collide on the same chunk_id - the second call
        blocks (up to connect()'s timeout) until the first commits.
        """
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_count = connection.execute(
                "SELECT COUNT(*) FROM reference_chunks WHERE document = ?", (document,)
            ).fetchone()[0]
            for offset, text in enumerate(chunks):
                chunk_id = f"{document}#{existing_count + offset + 1}"
                connection.execute(
                    """
                    INSERT INTO reference_chunks(id, document, title, text, revision, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        text = excluded.text,
                        revision = reference_chunks.revision + 1,
                        updated_at = excluded.updated_at
                    """,
                    (chunk_id, document, title, text, now),
                )
        return self.list_reference_chunks()

    def list_reference_chunks(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, document, title, text, revision, updated_at
                FROM reference_chunks
                ORDER BY document, id COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def stale_reference_chunks(self, model: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.document, c.title, c.text, c.revision
                FROM reference_chunks c
                LEFT JOIN reference_embeddings e ON e.chunk_id = c.id
                WHERE e.chunk_id IS NULL
                   OR e.model != ?
                   OR e.revision != c.revision
                ORDER BY c.id COLLATE NOCASE
                """,
                (model,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_reference_embeddings(
        self, model: str, chunks: List[Dict[str, Any]], vectors: List[List[float]]
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("Ollama returned an unexpected number of embeddings")
        now = utc_now()
        with self.connect() as connection:
            for chunk, vector in zip(chunks, vectors):
                current = connection.execute(
                    "SELECT revision FROM reference_chunks WHERE id = ?",
                    (chunk["id"],),
                ).fetchone()
                if current is None or current["revision"] != chunk["revision"]:
                    continue
                connection.execute(
                    """
                    INSERT INTO reference_embeddings(chunk_id, model, revision, vector, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        model = excluded.model,
                        revision = excluded.revision,
                        vector = excluded.vector,
                        updated_at = excluded.updated_at
                    """,
                    (chunk["id"], model, chunk["revision"], json.dumps(vector), now),
                )

    def indexed_reference_chunks(self, model: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.document, c.title, c.text, e.vector
                FROM reference_chunks c
                JOIN reference_embeddings e ON e.chunk_id = c.id
                WHERE e.model = ? AND e.revision = c.revision
                """,
                (model,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "document": row["document"],
                "title": row["title"],
                "text": row["text"],
                "vector": json.loads(row["vector"]),
            }
            for row in rows
        ]

    def reference_counts(self, model: str) -> Dict[str, int]:
        with self.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM reference_chunks").fetchone()[0]
            indexed = connection.execute(
                """
                SELECT COUNT(*)
                FROM reference_chunks c
                JOIN reference_embeddings e ON e.chunk_id = c.id
                WHERE e.model = ? AND e.revision = c.revision
                """,
                (model,),
            ).fetchone()[0]
        return {"total": total, "indexed": indexed, "pending": total - indexed}


def ollama_embed(url: str, model: str, texts: List[str]) -> List[List[float]]:
    response = requests.post(
        f"{url.rstrip('/')}/api/embed",
        json={"model": model, "input": texts, "truncate": True},
        timeout=300,
    )
    response.raise_for_status()
    vectors = response.json().get("embeddings") or []
    return [[float(value) for value in vector] for vector in vectors]


def cosine_distance(left: List[float], right: List[float]) -> float:
    if len(left) != len(right) or not left:
        return float("inf")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return float("inf")
    return 1.0 - (dot / (left_norm * right_norm))


def cosine_similarity(left: List[float], right: List[float]) -> float:
    distance = cosine_distance(left, right)
    if math.isinf(distance):
        return 0.0
    return max(-1.0, min(1.0, 1.0 - distance))


STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in",
    "is", "it", "of", "on", "or", "shall", "should", "system", "that", "the",
    "this", "to", "what", "when", "where", "which", "who", "will", "with",
}

DOMAIN_WEIGHT = {
    "nominal": 1.2,
    "degraded": 1.5,
    "maximum": 1.1,
    "minimum": 1.1,
    "peak": 1.3,
    "continuous": 1.2,
    "transient": 1.5,
    "standby": 1.4,
    "rated": 1.2,
    "provide": 0.5,
    "support": 0.8,
}

PARAMETER_NOUNS = {
    "voltage", "current", "power", "resistance", "frequency", "temperature",
    "pressure", "torque", "speed", "force", "supply", "load",
}

BROAD_SUMMARY_TERMS = {
    "architecture", "capability", "capabilities", "feature", "features", "function",
    "functions", "main", "overview", "purpose", "summary", "system", "under",
    "development", "does", "do",
}

META_REQUIREMENT_TERMS = {
    "identifier", "traceability", "traceable", "stakeholder", "stakeholders",
    "business", "documentation", "document", "requirement", "requirements",
    "coverage", "goal", "goals", "need", "needs",
}

FEATURE_ACTION_TERMS = {
    "activate", "control", "deactivate", "detect", "limit", "manage", "measure",
    "monitor", "operate", "protect", "provide", "reject", "respond", "support",
    "supply", "tolerate", "update", "withstand",
}

UNIT_PATTERN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(ma|a|mv|kv|v|kw|w|khz|mhz|hz|ohm|f|uf|h|c|k|kg|g|mm|km|m|ms|s|n|pa|bar)\b",
    re.IGNORECASE,
)

# A requirement can express a parameter purely via its unit (e.g. "12 V") without ever
# using the parameter noun itself (e.g. "voltage") - without this mapping, structural_score
# would penalize the requirement that actually states the value in favor of requirements
# that merely mention the parameter noun in passing.
UNIT_TO_PARAMETER = {
    "v": "voltage",
    "mv": "voltage",
    "kv": "voltage",
    "a": "current",
    "ma": "current",
    "w": "power",
    "kw": "power",
    "ohm": "resistance",
    "hz": "frequency",
    "khz": "frequency",
    "mhz": "frequency",
    "c": "temperature",
    "k": "temperature",
    "n": "force",
    "pa": "pressure",
    "bar": "pressure",
}

TOKEN_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]*|\d+(?:\.\d+)?")


def normalize_token(token: str) -> str:
    token = token.lower().strip("-_")
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def extract_keywords(text: str) -> List[str]:
    return [
        normalize_token(match.group(0))
        for match in TOKEN_PATTERN.finditer(text)
        if normalize_token(match.group(0)) and normalize_token(match.group(0)) not in STOP_WORDS
    ]


def extract_units(text: str) -> set:
    return {match.group(2).lower() for match in UNIT_PATTERN.finditer(text)}


def extract_quantities(text: str) -> List[Dict[str, Any]]:
    return [
        {
            "value": match.group(1),
            "unit": match.group(2),
            "text": f"{match.group(1)}{match.group(2)}",
        }
        for match in UNIT_PATTERN.finditer(text)
    ]


def extract_parameters(text: str) -> set:
    named = {token for token in extract_keywords(text) if token in PARAMETER_NOUNS}
    from_units = {UNIT_TO_PARAMETER[unit] for unit in extract_units(text) if unit in UNIT_TO_PARAMETER}
    return named | from_units


def is_broad_summary_query(query: str) -> bool:
    keywords = set(extract_keywords(query))
    if extract_units(query) or extract_parameters(query):
        return False
    return bool(keywords & BROAD_SUMMARY_TERMS)


def build_idf(texts: List[str]) -> Dict[str, float]:
    total = max(len(texts), 1)
    doc_frequency: Counter = Counter()
    for text in texts:
        doc_frequency.update(set(extract_keywords(text)))
    return {
        token: math.log((total + 1) / (count + 1)) + 1.0
        for token, count in doc_frequency.items()
    }


def keyword_score(query_keywords: List[str], requirement_text: str, idf: Dict[str, float]) -> float:
    requirement_keywords = set(extract_keywords(requirement_text))
    if not query_keywords:
        return 0.0
    score = 0.0
    total_weight = 0.0
    for keyword in query_keywords:
        weight = idf.get(keyword, 1.0) * DOMAIN_WEIGHT.get(keyword, 1.0)
        total_weight += weight
        if keyword in requirement_keywords:
            score += weight
    return score / total_weight if total_weight else 0.0


def extract_phrases(text: str, n: int = 2) -> set:
    tokens = extract_keywords(text)
    return {" ".join(tokens[index:index + n]) for index in range(len(tokens) - n + 1)}


def phrase_score(query: str, requirement_text: str) -> float:
    query_phrases = extract_phrases(query, n=2)
    requirement_phrases = extract_phrases(requirement_text, n=2)
    if not query_phrases:
        return 0.0
    return 1.0 if query_phrases & requirement_phrases else 0.0


def parameter_penalty(query: str, requirement_text: str) -> float:
    query_units = extract_units(query)
    requirement_units = extract_units(requirement_text)
    query_parameters = extract_parameters(query)
    requirement_parameters = extract_parameters(requirement_text)

    extra_units = requirement_units - query_units
    extra_parameters = requirement_parameters - query_parameters
    return min((len(extra_units) + len(extra_parameters)) * 0.05, 0.25)


def structural_score(query: str, requirement_text: str) -> float:
    query_units = extract_units(query)
    if query_units:
        requirement_units = extract_units(requirement_text)
        return len(query_units & requirement_units) / len(query_units)

    query_parameters = extract_parameters(query)
    if query_parameters:
        requirement_parameters = extract_parameters(requirement_text)
        return len(query_parameters & requirement_parameters) / len(query_parameters)

    return 0.5


def ranked_matches(
    indexed: List[Dict[str, Any]],
    query_vector: List[float],
    query: str = "",
    min_similarity: float = 0.30,
) -> Dict[str, Any]:
    threshold = max(0.0, min(1.0, min_similarity))
    idf = build_idf([item["text"] for item in indexed])
    query_keywords = extract_keywords(query)
    ranked = sorted(
        (
            score_requirement(item, query_vector, query, query_keywords, idf)
            for item in indexed
        ),
        key=lambda item: item["score"],
        reverse=True,
    )
    best_score = ranked[0]["score"] if ranked else 0.0
    relative_floor = max(threshold, best_score - 0.18)
    matches = [item for item in ranked if item["score"] >= relative_floor]
    return {
        "ranked": ranked,
        "matches": matches,
        "discarded": len(ranked) - len(matches),
        "threshold": threshold,
        "best_similarity": best_score,
        "best_score": best_score,
    }


def score_requirement(
    item: Dict[str, Any],
    query_vector: List[float],
    query: str,
    query_keywords: List[str],
    idf: Dict[str, float],
) -> Dict[str, Any]:
    embedding = cosine_similarity(query_vector, item["vector"])
    keyword = keyword_score(query_keywords, item["text"], idf)
    phrase = phrase_score(query, item["text"])
    struct = structural_score(query, item["text"])
    penalty = parameter_penalty(query, item["text"])
    score = (
        0.35 * embedding
        + 0.40 * keyword
        + 0.15 * struct
        + 0.10 * phrase
        - penalty
    )
    score = max(0.0, min(1.0, score))
    return {
        "id": item["id"],
        "text": item["text"],
        "source": item["source"],
        "distance": 1.0 - embedding,
        "similarity": score,
        "score": score,
        "breakdown": {
            "embedding": round(embedding, 3),
            "keyword": round(keyword, 3),
            "structure": round(struct, 3),
            "phrase": round(phrase, 3),
            "penalty": round(-penalty, 3),
        },
    }


def lexical_overlap(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def feature_salience(item: Dict[str, Any], query_vector: List[float]) -> Dict[str, Any]:
    keywords = set(extract_keywords(item["text"]))
    embedding = cosine_similarity(query_vector, item["vector"])
    action_hits = len(keywords & FEATURE_ACTION_TERMS)
    parameter_hits = len(keywords & PARAMETER_NOUNS) + len(extract_units(item["text"]))
    meta_hits = len(keywords & META_REQUIREMENT_TERMS)
    specificity = min(len(keywords) / 12.0, 1.0)
    score = (
        0.20 * embedding
        + 0.25 * specificity
        + min(action_hits * 0.12, 0.30)
        + min(parameter_hits * 0.08, 0.24)
        - min(meta_hits * 0.18, 0.45)
    )
    score = max(0.0, min(1.0, score))
    return {
        "id": item["id"],
        "text": item["text"],
        "source": item["source"],
        "distance": 1.0 - embedding,
        "similarity": score,
        "score": score,
        "keywords": keywords,
        "breakdown": {
            "embedding": round(embedding, 3),
            "specificity": round(specificity, 3),
            "actions": action_hits,
            "parameters": parameter_hits,
            "meta_penalty": -meta_hits,
        },
    }


def broad_summary_sources(
    indexed: List[Dict[str, Any]],
    query_vector: List[float],
    limit: int = 6,
) -> Dict[str, Any]:
    ranked = sorted(
        (feature_salience(item, query_vector) for item in indexed),
        key=lambda item: item["score"],
        reverse=True,
    )
    selected: List[Dict[str, Any]] = []
    for item in ranked:
        if item["score"] < 0.18:
            continue
        if any(lexical_overlap(item["keywords"], chosen["keywords"]) > 0.62 for chosen in selected):
            continue
        selected.append(item)
        if len(selected) >= max(1, min(limit, 8)):
            break

    if not selected:
        selected = ranked[: max(1, min(limit, 8))]

    for item in selected:
        item.pop("keywords", None)

    return {
        "ranked": ranked,
        "matches": selected,
        "discarded": max(len(indexed) - len(selected), 0),
        "threshold": 0.18,
        "best_similarity": selected[0]["score"] if selected else 0.0,
        "best_score": selected[0]["score"] if selected else 0.0,
        "broad_summary": True,
    }


class IndexCoordinator:
    """Background embedding indexer, generalized over which store methods it drives.

    Defaults to the requirement-embedding methods; pass `stale_fn`/`save_fn`/
    `count_fn` bound to the reference-chunk methods to run a second, independent
    coordinator over reference material (SPEC.md Tier 2) without duplicating
    the threading/status logic.
    """

    def __init__(
        self,
        store: RequirementStore,
        embedder: Callable[[str, str, List[str]], List[List[float]]] = ollama_embed,
        stale_fn: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
        save_fn: Optional[Callable[[str, List[Dict[str, Any]], List[List[float]]], None]] = None,
        count_fn: Optional[Callable[[str], Dict[str, int]]] = None,
        thread_name: str = "requirement-indexer",
    ):
        self.store = store
        self.embedder = embedder
        self.stale_fn = stale_fn or store.stale_requirements
        self.save_fn = save_fn or store.save_embeddings
        self.count_fn = count_fn or store.counts
        self.thread_name = thread_name
        self.lock = threading.Lock()
        self.running = False
        self.rerun = False
        self.error: Optional[str] = None
        self.model = os.environ.get("OLLAMA_EMBED_MODEL", "embeddinggemma")
        self.ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")

    def schedule(self, model: str, ollama_url: str) -> None:
        with self.lock:
            self.model = model
            self.ollama_url = ollama_url
            self.rerun = True
            if self.running:
                return
            self.running = True
            threading.Thread(target=self._run, daemon=True, name=self.thread_name).start()

    def _run(self) -> None:
        try:
            while True:
                with self.lock:
                    self.rerun = False
                    model = self.model
                    ollama_url = self.ollama_url
                stale = self.stale_fn(model)
                if stale:
                    vectors = self.embedder(
                        ollama_url,
                        model,
                        [item["text"] for item in stale],
                    )
                    self.save_fn(model, stale, vectors)
                with self.lock:
                    if not self.rerun and not self.stale_fn(self.model):
                        self.error = None
                        self.running = False
                        return
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                self.running = False

    def status(self) -> Dict[str, Any]:
        with self.lock:
            model = self.model
            running = self.running
            error = self.error
        counts = self.count_fn(model)
        state = "error" if error else ("indexing" if running else "ready")
        if counts["total"] == 0:
            state = "empty"
        elif counts["pending"] and not running and not error:
            state = "pending"
        return {
            "state": state,
            "model": model,
            "ollama_url": self.ollama_url,
            "error": error,
            **counts,
        }


def parse_import(filename: str, content: bytes) -> List[Requirement]:
    text = content.decode("utf-8-sig", errors="ignore")
    items: List[Requirement] = []
    if (filename or "").lower().endswith(".json"):
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("requirements")
        if not isinstance(data, list):
            raise ValueError("JSON must be a list or an object with a requirements list")
        for index, row in enumerate(data, start=1):
            if isinstance(row, str):
                items.append(Requirement(id=f"REQ-{index:03d}", text=row))
            elif isinstance(row, dict):
                items.append(
                    Requirement(
                        id=str(row.get("id") or row.get("requirement_id") or f"REQ-{index:03d}"),
                        text=str(
                            row.get("text")
                            or row.get("requirement")
                            or row.get("description")
                            or ""
                        ),
                        source=row.get("source"),
                    )
                )
    else:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("CSV is empty or missing a header")
        columns = {column.lower(): column for column in reader.fieldnames}
        text_column = (
            columns.get("text")
            or columns.get("requirement")
            or columns.get("description")
        )
        if not text_column:
            raise ValueError("CSV needs a text, requirement, or description column")
        id_column = columns.get("id") or columns.get("requirement_id")
        source_column = columns.get("source")
        for index, row in enumerate(reader, start=1):
            items.append(
                Requirement(
                    id=str(row.get(id_column) or f"REQ-{index:03d}") if id_column else f"REQ-{index:03d}",
                    text=str(row.get(text_column) or ""),
                    source=str(row.get(source_column) or "") if source_column else None,
                )
            )
    return [item for item in items if item.text.strip()]


def extract_summary(raw: str) -> str:
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
            if payload.get("summary"):
                return str(payload["summary"]).strip()
        except json.JSONDecodeError:
            pass
    return text


def compact_text(text: str, limit: int = 220) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    if limit <= 3:
        return normalized[:limit]
    return normalized[: limit - 3].rstrip() + "..."


def select_summary_sources(matches: List[Dict[str, Any]], limit: int = 4) -> List[Dict[str, Any]]:
    bounded_limit = max(1, min(limit, 6))
    return matches[:bounded_limit]


def build_summary_context(sources: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"[{item['id']}] score={item.get('score', item.get('similarity', 0.0)):.2f}: "
        f"{compact_text(item['text'])}"
        for item in sources
    )


def fallback_summary(query: str, sources: List[Dict[str, Any]], warning: Optional[str] = None) -> str:
    if not sources:
        return (
            "I could not find requirements that are relevant enough to answer this "
            "question reliably."
        )

    if is_broad_summary_query(query):
        bullets = "\n".join(
            f"- {compact_text(source['text'], 210)} [{source['id']}]"
            for source in sources
        )
        return (
            "The main product capabilities reflected in the selected requirements are:\n"
            f"{bullets}\n"
            "This overview excludes requirement-management or traceability items unless they "
            "are among the strongest product-relevant evidence."
        )

    value_summary = summarize_quantitative_answer(query, sources)
    if value_summary:
        return value_summary

    lead = f"The most relevant evidence for '{query}' is "
    if len(sources) == 1:
        source = sources[0]
        return f"{lead}[{source['id']}]: {compact_text(source['text'], 320)}"

    evidence = "; ".join(
        f"[{source['id']}] {compact_text(source['text'], 180)}"
        for source in sources
    )
    return (
        f"{lead}{len(sources)} requirements. If they do not state one unique answer, "
        f"treat the result as non-unique and review the cited requirements: {evidence}"
    )


def summarize_quantitative_answer(query: str, sources: List[Dict[str, Any]]) -> Optional[str]:
    query_parameters = extract_parameters(query)
    if not query_parameters and not extract_units(query):
        return None

    statements = []
    seen_values = set()
    for source in sources:
        quantities = extract_quantities(source["text"])
        if not quantities:
            continue
        params = extract_parameters(source["text"])
        if query_parameters and params and not (query_parameters & params):
            continue
        quantity_text = ", ".join(quantity["text"] for quantity in quantities)
        seen_values.add(quantity_text.lower())
        statements.append(
            {
                "id": source["id"],
                "quantity": quantity_text,
                "text": compact_text(source["text"], 230),
            }
        )

    if not statements:
        return None

    parameter_label = ", ".join(sorted(query_parameters)) or "requested quantity"
    if len(seen_values) == 1:
        first = statements[0]
        return (
            f"The requirement evidence indicates {parameter_label} = {first['quantity']} "
            f"based on [{first['id']}]. {first['text']}"
        )

    evidence = " ".join(
        f"[{statement['id']}] states {statement['quantity']} ({statement['text']})."
        for statement in statements
    )
    return (
        f"There is no single unique {parameter_label} value in the matching requirements. "
        f"{evidence}"
    )


def ollama_summary(
    url: str,
    model: str,
    question: str,
    sources: List[Dict[str, Any]],
    timeout: int,
    fewshot_prefix: str = "",
    reference_context: str = "",
) -> str:
    user_content = f"Question: {question}\n\nRelevant requirements:\n{build_summary_context(sources)}"
    if reference_context:
        user_content += f"\n\nREFERENCE MATERIAL (context only, not a requirement):\n{reference_context}"
    response = requests.post(
        f"{url.rstrip('/')}/api/chat",
        json={
            "model": model,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_ctx": 2048,
                "num_predict": 160,
                "top_k": 20,
                "top_p": 0.9,
            },
            "messages": [
                {
                    "role": "system",
                    "content": fewshot_prefix + EXECUTIVE_SUMMARY_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return str((response.json().get("message") or {}).get("content") or "").strip()


_SENTENCE_PATTERN = re.compile(r"[^.!?]+[.!?]*")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def _normalize_for_comparison(text: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", text.strip().lower())


def summary_violates_contract(answer: str, sources: List[Dict[str, Any]]) -> Optional[str]:
    """Check an LLM-generated executive summary against SPEC.md 2.3's output contract.

    The only hard-gated rule is "no verbatim requirement sentences" (quantitative
    facts like numbers/units are fine to restate). Returns a violation reason, or
    None if the answer passes.
    """
    normalized_answer = _normalize_for_comparison(answer)
    for source in sources:
        for sentence in _SENTENCE_PATTERN.findall(source.get("text", "")):
            normalized_sentence = _normalize_for_comparison(sentence)
            if len(normalized_sentence) >= 30 and normalized_sentence in normalized_answer:
                return f"verbatim quote from {source.get('id')}"
    return None


async def _embed_query(
    store: RequirementStore,
    indexer: "IndexCoordinator",
    query: str,
    embedding_model: str,
    ollama_url: str,
    min_similarity: float,
):
    query = query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Enter a search question")

    indexed = await asyncio.to_thread(store.indexed_requirements, embedding_model)
    if not indexed:
        status = indexer.status()
        detail = status.get("error") or "The requirement index is not ready yet"
        raise HTTPException(status_code=409, detail=detail)
    try:
        query_vectors = await asyncio.to_thread(
            ollama_embed, ollama_url, embedding_model, [query],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Embedding query failed: {exc}") from exc

    ranking = ranked_matches(indexed, query_vectors[0], query, min_similarity)
    return query, indexed, query_vectors[0], ranking


_VALID_VERDICTS = ("answers", "partially_answers", "does_not_answer")


def _fallback_verdict(candidate_id: str) -> Dict[str, Any]:
    return {
        "id": candidate_id,
        "verdict": "does_not_answer",
        "justification": "Verification unavailable for this candidate.",
        "facet": None,
    }


def _is_valid_verdict(item: Dict[str, Any]) -> bool:
    return item.get("verdict") in _VALID_VERDICTS


async def verify_candidates(
    llm_complete: Callable[..., Awaitable[str]],
    provider: str,
    model: str,
    ollama_url: str,
    query: str,
    candidates: List[Dict[str, Any]],
    fewshot_prefix: str = "",
    reference_context: str = "",
) -> Tuple[List[Dict[str, Any]], bool]:
    """Judge each candidate against the question (SPEC.md 2.2: verification-reasoning step).

    Returns (verdicts, unverified). `verdicts` is one entry per candidate, in the
    same order as `candidates`, each with `id`/`verdict`/`justification`/`facet`.
    `unverified` is True only when the LLM call itself failed (provider down,
    etc.) - in that case `verdicts` is empty and the caller should degrade to
    similarity-only results rather than claim to know the answer.
    """
    candidate_ids = [item["id"] for item in candidates]
    listing = "\n".join(f"[{item['id']}] {item['text']}" for item in candidates)
    user_message = f"Question: {query}\n\nCandidates:\n{listing}"
    if reference_context:
        user_message += f"\n\nREFERENCE MATERIAL (context only, not candidates):\n{reference_context}"
    system_message = fewshot_prefix + SEARCH_VERIFICATION_PROMPT

    try:
        raw = await llm_complete(provider, model, system_message, user_message, ollama_url)
    except Exception:
        return [], True

    try:
        parsed = extract_json(raw)
        verdicts = parsed.get("verdicts", [])
        if not isinstance(verdicts, list):
            verdicts = []
    except Exception:
        verdicts = []

    reconciled = reconcile_by_id(candidate_ids, verdicts, "id", _fallback_verdict, _is_valid_verdict)
    return reconciled, False


async def _no_fewshot_examples() -> str:
    return ""


def create_requirements_router(
    llm_complete: Callable[..., Awaitable[str]],
    database_path: Optional[Path] = None,
    fetch_fewshot_examples: Callable[[], Awaitable[str]] = _no_fewshot_examples,
) -> APIRouter:
    store = RequirementStore(
        database_path
        or Path(os.environ.get("REQUIREMENTS_DB_PATH", Path(__file__).parent / "data" / "requirements.db"))
    )
    indexer = IndexCoordinator(store)
    reference_indexer = IndexCoordinator(
        store,
        stale_fn=store.stale_reference_chunks,
        save_fn=store.save_reference_embeddings,
        count_fn=store.reference_counts,
        thread_name="reference-indexer",
    )
    router = APIRouter(tags=["requirements"])

    async def build_reference_context(embedding_model: str, ollama_url: str, query_vector: List[float]) -> str:
        indexed = await asyncio.to_thread(store.indexed_reference_chunks, embedding_model)
        if not indexed:
            return ""
        ranked = rank_reference_chunks(indexed, query_vector, min_similarity=0.30)
        top = ranked[:3]
        if not top:
            return ""
        return "\n".join(f"[{item['document']}] {compact_text(item['text'], 300)}" for item in top)

    @router.get("/requirements")
    async def list_requirements():
        return {
            "requirements": await asyncio.to_thread(store.list_requirements),
            "index": await asyncio.to_thread(indexer.status),
        }

    @router.put("/requirements")
    async def save_requirements(body: SaveRequirementsBody):
        try:
            requirements = await asyncio.to_thread(
                store.replace_requirements, body.requirements
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        indexer.schedule(body.embedding_model, body.ollama_url)
        return {"requirements": requirements, "index": indexer.status()}

    @router.post("/requirements/import")
    async def import_requirements(
        file: UploadFile = File(...),
        embedding_model: str = "embeddinggemma",
        ollama_url: str = "http://localhost:11434",
    ):
        try:
            parsed = parse_import(file.filename or "", await file.read())
            requirements = await asyncio.to_thread(store.replace_requirements, parsed)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Import failed: {exc}") from exc
        indexer.schedule(embedding_model, ollama_url)
        return {"requirements": requirements, "index": indexer.status()}

    @router.get("/index/status")
    async def index_status():
        return await asyncio.to_thread(indexer.status)

    @router.post("/index/rebuild")
    async def index_rebuild(body: IndexRebuildBody = IndexRebuildBody()):
        indexer.schedule(body.embedding_model, body.ollama_url)
        return await asyncio.to_thread(indexer.status)

    @router.post("/corpus/references")
    async def ingest_reference(body: ReferenceIngestBody):
        chunks = chunk_reference_text(body.text)
        if not chunks:
            raise HTTPException(status_code=400, detail="No content to ingest after chunking")
        await asyncio.to_thread(store.add_reference_chunks, body.document, body.title, chunks)
        reference_indexer.schedule(body.embedding_model, body.ollama_url)
        return {
            "document": body.document,
            "chunks_added": len(chunks),
            "index": await asyncio.to_thread(reference_indexer.status),
        }

    @router.get("/corpus/references")
    async def list_references():
        return {
            "chunks": await asyncio.to_thread(store.list_reference_chunks),
            "index": await asyncio.to_thread(reference_indexer.status),
        }

    @router.get("/corpus/references/index/status")
    async def reference_index_status():
        return await asyncio.to_thread(reference_indexer.status)

    @router.post("/search")
    async def search(body: SearchBody):
        _, _, query_vector, ranking = await _embed_query(
            store, indexer, body.query, body.embedding_model, body.ollama_url, body.min_similarity
        )
        matches = ranking["matches"]

        if not matches:
            return {
                "requirements": [],
                "requirement": None,
                "answered": False,
                "message": (
                    "No requirement was similar enough to the query. "
                    f"Best similarity was {ranking['best_similarity']:.2f}; "
                    f"minimum is {ranking['threshold']:.2f}."
                ),
                "discarded": ranking["discarded"],
                "threshold": ranking["threshold"],
                "best_similarity": ranking["best_similarity"],
            }

        fewshot_prefix = await fetch_fewshot_examples()
        reference_context = await build_reference_context(body.embedding_model, body.ollama_url, query_vector)
        verdicts, unverified = await verify_candidates(
            llm_complete,
            body.provider,
            body.llm_model,
            body.ollama_url,
            body.query,
            matches,
            fewshot_prefix,
            reference_context,
        )

        if unverified:
            return {
                "requirements": [
                    {**item, "verdict": None, "justification": None, "facet": None}
                    for item in matches
                ],
                "requirement": matches[0],
                "answered": None,
                "unverified": True,
                "message": "Verification LLM unavailable; showing similarity-ranked matches without verdicts.",
                "discarded": ranking["discarded"],
                "threshold": ranking["threshold"],
                "best_similarity": ranking["best_similarity"],
            }

        enriched = [
            {**item, "verdict": verdict["verdict"], "justification": verdict["justification"], "facet": verdict.get("facet")}
            for item, verdict in zip(matches, verdicts)
        ]
        answering = [item for item in enriched if item["verdict"] in ("answers", "partially_answers")]

        return {
            "requirements": enriched,
            "requirement": answering[0] if len(answering) == 1 else None,
            "answered": bool(answering),
            "message": None if answering else "No requirement in the current set answers this question.",
            "discarded": ranking["discarded"],
            "threshold": ranking["threshold"],
            "best_similarity": ranking["best_similarity"],
        }

    @router.post("/summary")
    async def summary(body: SummaryBody):
        query, indexed, query_vector, ranking = await _embed_query(
            store, indexer, body.query, body.embedding_model, body.ollama_url, body.min_similarity
        )

        if is_broad_summary_query(query):
            ranking = broad_summary_sources(indexed, query_vector, body.summary_top_k + 3)
        matches = ranking["matches"]
        summary_sources = select_summary_sources(matches, body.summary_top_k)
        if not summary_sources:
            return {
                "summary_text": fallback_summary(query, []),
                "source_ids": [],
                "sources": [],
                "discarded": ranking["discarded"],
                "threshold": ranking["threshold"],
                "best_similarity": ranking["best_similarity"],
                "ambiguous": True,
                "degraded": True,
                "degraded_reason": "no relevant requirements found",
            }

        enable_llm_summary = os.environ.get("ENABLE_LLM_SUMMARY", "").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        degraded_reason = None
        if enable_llm_summary:
            summary_timeout = int(os.environ.get("SUMMARY_TIMEOUT", "12"))
            fewshot_prefix = await fetch_fewshot_examples()
            reference_context = await build_reference_context(body.embedding_model, body.ollama_url, query_vector)
            try:
                answer = await asyncio.to_thread(
                    ollama_summary,
                    body.ollama_url,
                    body.llm_model,
                    query,
                    summary_sources,
                    summary_timeout,
                    fewshot_prefix,
                    reference_context,
                )
                violation = summary_violates_contract(answer, summary_sources)
                if violation:
                    degraded_reason = violation
                    answer = fallback_summary(query, summary_sources, violation)
                degraded = bool(violation)
            except Exception as exc:
                degraded_reason = str(exc)
                answer = fallback_summary(query, summary_sources, str(exc))
                degraded = True
        else:
            answer = fallback_summary(query, summary_sources)
            degraded = True
            degraded_reason = "LLM summary disabled (ENABLE_LLM_SUMMARY not set)"

        sources = [
            {key: item[key] for key in ("id", "text", "source", "distance", "similarity", "score", "breakdown")}
            for item in summary_sources
        ]
        return {
            "summary_text": answer,
            "source_ids": [item["id"] for item in summary_sources],
            "sources": sources,
            "discarded": ranking["discarded"],
            "threshold": ranking["threshold"],
            "best_similarity": ranking["best_similarity"],
            "ambiguous": len(summary_sources) != 1,
            "degraded": degraded,
            "degraded_reason": degraded_reason,
        }

    router.store = store
    router.indexer = indexer
    router.reference_indexer = reference_indexer
    return router
