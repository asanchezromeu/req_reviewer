import os
import io
import json
import uuid
import csv
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

import requests
from fastapi import Depends, FastAPI, APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:
    AsyncIOMotorClient = None

try:
    from .incose_rules import (
        INCOSE_RULES,
        INDIVIDUAL_SYSTEM_PROMPT,
        CONSISTENCY_SYSTEM_PROMPT,
        SUMMARIZER_SYSTEM_PROMPT,
    )
    from .retrieval import create_requirements_router
    from .auth import require_api_key
    from .llm_contract import extract_json
    from . import deterministic_review
    from . import model_registry
    from .testgen_prompts import (
        CONTEXT_ANALYSIS_PROMPT,
        DEFAULT_CATEGORY_STRATEGIES,
        CLASSIFY_AND_ASSESS_PROMPT,
        GENERATE_TEST_CASE_PROMPT,
        AUTHORIZE_FILL_PROMPT,
    )
    from .testgen_lint import check_anti_genericity
except ImportError:
    from incose_rules import (
        INCOSE_RULES,
        INDIVIDUAL_SYSTEM_PROMPT,
        CONSISTENCY_SYSTEM_PROMPT,
        SUMMARIZER_SYSTEM_PROMPT,
    )
    from retrieval import create_requirements_router
    from auth import require_api_key
    from llm_contract import extract_json
    import deterministic_review
    import model_registry
    from testgen_prompts import (
        CONTEXT_ANALYSIS_PROMPT,
        DEFAULT_CATEGORY_STRATEGIES,
        CLASSIFY_AND_ASSESS_PROMPT,
        GENERATE_TEST_CASE_PROMPT,
        AUTHORIZE_FILL_PROMPT,
    )
    from testgen_lint import check_anti_genericity

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("reqiq")

class MemoryDeleteResult:
    def __init__(self, deleted_count: int):
        self.deleted_count = deleted_count


class MemoryCursor:
    def __init__(self, documents: List[Dict[str, Any]]):
        self.documents = documents

    def sort(self, key: str, direction: int):
        self.documents.sort(key=lambda item: item.get(key, ""), reverse=direction < 0)
        return self

    async def to_list(self, limit: int):
        return self.documents[:limit]


class MemoryCollection:
    def __init__(self):
        self.documents: List[Dict[str, Any]] = []

    async def insert_one(self, document: Dict[str, Any]):
        self.documents.append(dict(document))

    async def find_one(self, query: Dict[str, Any], projection: Optional[Dict[str, int]] = None):
        document = next((item for item in self.documents if _matches_query(item, query)), None)
        return _apply_projection(document, projection)

    def find(self, query: Dict[str, Any], projection: Optional[Dict[str, int]] = None):
        rows = [_apply_projection(item, projection) for item in self.documents if _matches_query(item, query)]
        return MemoryCursor([row for row in rows if row is not None])

    async def delete_one(self, query: Dict[str, Any]):
        for index, item in enumerate(self.documents):
            if _matches_query(item, query):
                self.documents.pop(index)
                return MemoryDeleteResult(1)
        return MemoryDeleteResult(0)

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any]):
        document = next((item for item in self.documents if _matches_query(item, query)), None)
        if document is not None:
            document.update(update.get("$set", {}))


class MemoryDatabase:
    def __init__(self):
        self.system_prompts = MemoryCollection()
        self.training_examples = MemoryCollection()
        self.training_datasets = MemoryCollection()
        self.distillation_jobs = MemoryCollection()
        self.model_registry = MemoryCollection()
        self.test_context_versions = MemoryCollection()
        self.category_strategies = MemoryCollection()
        self.test_cases = MemoryCollection()
        self.test_gaps = MemoryCollection()


def _matches_query(document: Dict[str, Any], query: Dict[str, Any]) -> bool:
    return all(document.get(key) == value for key, value in query.items())


def _apply_projection(
    document: Optional[Dict[str, Any]],
    projection: Optional[Dict[str, int]],
) -> Optional[Dict[str, Any]]:
    if document is None:
        return None
    result = dict(document)
    for key, included in (projection or {}).items():
        if not included:
            result.pop(key, None)
    return result


mongo_url = os.environ.get("MONGO_URL")
client = None
if mongo_url and AsyncIOMotorClient is not None:
    client = AsyncIOMotorClient(mongo_url)
    db = client[os.environ.get("DB_NAME", "req_reviewer")]
else:
    db = MemoryDatabase()
    logger.info("Using in-memory storage. Configure MONGO_URL for persistent data.")
app = FastAPI(title="ReqIQ Engine", version="1.0.0")
api = APIRouter()


# ---------- Models ----------

AVAILABLE_MODELS = {
    "ollama": [
        {"id": "gemma3:1b", "label": "Gemma 3 1B (local default)"},
        {"id": "gemma3:4b", "label": "Gemma 3 4B"},
        {"id": "qwen3:4b", "label": "Qwen 3 4B"},
        {"id": "llama3.2:3b", "label": "Llama 3.2 3B"},
    ],
    "openai": [
        {"id": "gpt-4o-mini", "label": "GPT-4o mini (fast)"},
        {"id": "gpt-4o", "label": "GPT-4o"},
        {"id": "gpt-5-mini", "label": "GPT-5 mini"},
        {"id": "gpt-5", "label": "GPT-5"},
        {"id": "gpt-5.4", "label": "GPT-5.4 (recommended)"},
    ],
    "anthropic": [
        {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5 (fast)"},
        {"id": "claude-sonnet-4-5-20250929", "label": "Claude Sonnet 4.5"},
        {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6 (recommended)"},
    ],
    "gemini": [
        {"id": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite (fast)"},
        {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
        {"id": "gemini-3-flash-preview", "label": "Gemini 3 Flash"},
        {"id": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro (recommended)"},
    ],
}


class ReviewRequirementBody(BaseModel):
    text: str
    requirement_id: Optional[str] = "REQ-X"
    provider: str
    model: str
    ollama_url: Optional[str] = None
    tailoring_prompt_id: Optional[str] = None


class ReviewSetBody(BaseModel):
    provider: str
    model: str
    ollama_url: Optional[str] = None
    tailoring_prompt_id: Optional[str] = None


class AskBody(BaseModel):
    question: str
    provider: str
    model: str
    ollama_url: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = None
    tailoring_prompt_id: Optional[str] = None


class SystemPrompt(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    kind: str  # "classifier" | "tailoring"
    system_prompt: str
    categories: List[str] = []  # only used when kind == "classifier"
    description: Optional[str] = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SystemPromptCreate(BaseModel):
    name: str
    kind: str
    system_prompt: str
    categories: List[str] = []
    description: Optional[str] = ""


class PromptGenerateBody(BaseModel):
    kind: str  # "classifier" | "tailoring"
    project_description: str
    provider: str
    model: str
    ollama_url: Optional[str] = None
    categories_hint: Optional[List[str]] = None


class ClassifyBody(BaseModel):
    provider: str
    model: str
    ollama_url: Optional[str] = None
    prompt_id: Optional[str] = None
    system_prompt: Optional[str] = None
    categories: Optional[List[str]] = None


class TrainingExample(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    label: str  # "good" or "bad"
    requirement_text: str
    explanation: Optional[str] = ""
    corrected_text: Optional[str] = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TrainingExampleCreate(BaseModel):
    label: str
    requirement_text: str
    explanation: Optional[str] = ""
    corrected_text: Optional[str] = ""


class TrainingDataset(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    sample_count: int
    samples: List[Dict[str, Any]]  # {"messages": [...]}-style JSONL rows
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DistillationJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    base_model: str
    dataset_id: str
    dataset_name: str
    openai_job_id: Optional[str] = None
    openai_file_id: Optional[str] = None
    fine_tuned_model: Optional[str] = None
    status: str = "queued"  # queued, validating_files, running, succeeded, failed, cancelled
    error: Optional[str] = None
    hyperparameters: Dict[str, Any] = {}
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DistillationStartBody(BaseModel):
    name: str
    dataset_id: str
    base_model: str = "gpt-4o-mini-2024-07-18"
    openai_api_key: str
    n_epochs: Optional[int] = 3


# ---------- Helpers ----------

async def llm_complete(
    provider: str,
    model: str,
    system_message: str,
    user_text: str,
    ollama_url: Optional[str] = None,
) -> str:
    if provider == "ollama":
        endpoint = (ollama_url or os.environ.get("OLLAMA_URL") or "http://localhost:11434").rstrip("/")

        def call_ollama() -> str:
            response = requests.post(
                f"{endpoint}/api/chat",
                json={
                    "model": model or os.environ.get("OLLAMA_MODEL", "gemma3:1b"),
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.05, "num_ctx": 8192, "num_predict": 2048},
                    "messages": [
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": user_text},
                    ],
                },
                timeout=180,
            )
            response.raise_for_status()
            payload = response.json()
            return str((payload.get("message") or {}).get("content") or "")

        return await asyncio.to_thread(call_ollama)

    def call_hosted() -> str:
        if provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not configured")
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "temperature": 0.05,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": user_text},
                    ],
                },
                timeout=180,
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])

        if provider == "anthropic":
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not configured")
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 4096,
                    "temperature": 0.05,
                    "system": system_message,
                    "messages": [{"role": "user", "content": user_text}],
                },
                timeout=180,
            )
            response.raise_for_status()
            return "".join(block.get("text", "") for block in response.json().get("content", []))

        if provider == "gemini":
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY is not configured")
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": api_key},
                json={
                    "system_instruction": {"parts": [{"text": system_message}]},
                    "contents": [{"role": "user", "parts": [{"text": user_text}]}],
                    "generationConfig": {
                        "temperature": 0.05,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=180,
            )
            response.raise_for_status()
            parts = response.json()["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts)

        raise RuntimeError(f"Unsupported provider: {provider}")

    return await asyncio.to_thread(call_hosted)


def parse_json_strict(text: str) -> Dict[str, Any]:
    """Extract JSON object from LLM output, tolerant to markdown fences."""
    return extract_json(text)


# Best-effort mapping from the deterministic scorer's flags to the INCOSE rule
# keys they're most related to, used only by the fallback below.
_FLAG_TO_RULES = {
    "Use shall/must": ("unambiguous", "consistent"),
    "Ambiguous wording": ("unambiguous",),
    "No measurable criterion": ("verifiable",),
    "May not be singular": ("singular",),
    "Context missing": ("complete",),
    "Too short": ("complete",),
    "Too long": ("complete",),
    "Incomplete placeholder": ("necessary", "complete"),
    "Traceability missing": ("traceable",),
}


def deterministic_fallback_review(req_id: str, req_text: str, reason: str) -> Dict[str, Any]:
    """Heuristic, non-LLM review used when the model's output couldn't be parsed.

    Per SPEC.md's architectural rule 4, malformed LLM output must degrade to
    the deterministic fallback, never crash or silently pass through as if it
    were a real (very bad) review.
    """
    review = deterministic_review.review_requirement(
        deterministic_review.Requirement(id=req_id, text=req_text)
    )
    flags = [] if review.flags == "OK" else [flag.strip() for flag in review.flags.split(",")]
    flagged_rules: Dict[str, str] = {}
    for flag in flags:
        for rule_key in _FLAG_TO_RULES.get(flag, ()):
            flagged_rules.setdefault(rule_key, flag)

    rules = {
        rule["key"]: {
            "score": 50 if rule["key"] in flagged_rules else review.score,
            "finding": flagged_rules.get(rule["key"], ""),
        }
        for rule in INCOSE_RULES
    }
    return {
        "requirement_id": req_id,
        "requirement_text": req_text,
        "overall_score": review.score,
        "summary": f"LLM review unavailable ({reason}); showing a heuristic fallback score instead.",
        "proposed_fix": review.improvement or req_text,
        "rules": rules,
        "fallback": True,
    }


# ---------- Routes ----------

@api.get("/")
async def root():
    return {"name": "ReqIQ Engine", "ok": True}


@api.get("/health")
async def health():
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")

    def check_ollama() -> Dict[str, Any]:
        try:
            response = requests.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=3)
            response.raise_for_status()
            return {"reachable": True}
        except requests.RequestException as exc:
            return {"reachable": False, "error": str(exc)}

    ollama_status = await asyncio.to_thread(check_ollama)
    return {
        "ok": True,
        "ollama": {"url": ollama_url, **ollama_status},
        "index": await asyncio.to_thread(requirements_indexer.status),
    }


@api.get("/models")
async def get_models():
    return AVAILABLE_MODELS


@api.get("/ollama/models")
async def get_ollama_models(url: str = "http://localhost:11434"):
    endpoint = url.rstrip("/")

    def fetch_models():
        response = requests.get(f"{endpoint}/api/tags", timeout=10)
        response.raise_for_status()
        return response.json()

    try:
        payload = await asyncio.to_thread(fetch_models)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not connect to Ollama at {endpoint}: {exc}",
        ) from exc

    models = []
    for item in payload.get("models", []):
        model_id = str(item.get("name") or item.get("model") or "").strip()
        if not model_id:
            continue
        details = item.get("details") or {}
        parameter_size = str(details.get("parameter_size") or "").strip()
        label = f"{model_id} ({parameter_size})" if parameter_size else model_id
        models.append({"id": model_id, "label": label})
    return models


@api.get("/incose/rules")
async def get_incose_rules():
    return INCOSE_RULES


# ----- Review -----

async def get_tailoring_prefix(tailoring_prompt_id: Optional[str]) -> str:
    if not tailoring_prompt_id:
        return ""
    doc = await db.system_prompts.find_one({"id": tailoring_prompt_id}, {"_id": 0})
    if not doc:
        return ""
    extra = (doc.get("system_prompt") or "").strip()
    if not extra:
        return ""
    return f"PROJECT TAILORING CONTEXT (apply this lens to your analysis):\n{extra}\n\n"


async def get_fewshot_prefix(max_per_label: int = 3) -> str:
    """SPEC.md Tier 1: inject curated good/bad examples as few-shot context.

    Flat and uncapped-by-relevance on purpose - Tier 1 is the curated corpus,
    not semantic retrieval (that's Tier 2, over reference material). Empty
    string when the corpus has nothing yet, so a fresh install is unaffected.
    """
    # Query each label separately so one label's volume can never crowd the
    # other out (a single combined "most recent 200, then split" fetch could
    # silently drop an entire label once the corpus skews heavily).
    good_cursor = db.training_examples.find({"label": "good"}, {"_id": 0}).sort("created_at", -1)
    bad_cursor = db.training_examples.find({"label": "bad"}, {"_id": 0}).sort("created_at", -1)
    good = await good_cursor.to_list(max_per_label)
    bad = await bad_cursor.to_list(max_per_label)
    if not good and not bad:
        return ""

    lines = ["CURATED EXAMPLES (for calibration only - do not copy verbatim):"]
    for ex in good:
        lines.append(f'GOOD: "{ex.get("requirement_text", "")}" - {ex.get("explanation", "")}'.rstrip(" -"))
    for ex in bad:
        line = f'BAD: "{ex.get("requirement_text", "")}" - {ex.get("explanation", "")}'.rstrip(" -")
        if ex.get("corrected_text"):
            line += f' (fix: "{ex["corrected_text"]}")'
        lines.append(line)
    return "\n".join(lines) + "\n\n"


async def analyze_one(
    provider: str,
    model: str,
    req_id: str,
    req_text: str,
    tailoring: str = "",
    ollama_url: Optional[str] = None,
) -> Dict[str, Any]:
    user = f"Requirement ID: {req_id}\nRequirement text: \"{req_text}\""
    try:
        sys_msg = (tailoring or "") + INDIVIDUAL_SYSTEM_PROMPT
        raw = await llm_complete(provider, model, sys_msg, user, ollama_url)
        parsed = parse_json_strict(raw)
        parsed["requirement_id"] = req_id
        parsed["requirement_text"] = req_text
        return parsed
    except Exception as e:
        logger.exception("analyze_one failed")
        fallback = deterministic_fallback_review(req_id, req_text, str(e))
        fallback["error"] = str(e)
        return fallback


@api.post("/review/requirement")
async def review_requirement_endpoint(body: ReviewRequirementBody):
    tailoring = await get_tailoring_prefix(body.tailoring_prompt_id) + await get_fewshot_prefix()
    return await analyze_one(
        body.provider,
        body.model,
        body.requirement_id or "REQ-X",
        body.text,
        tailoring,
        body.ollama_url,
    )


@api.post("/review/set")
async def review_set_endpoint(body: ReviewSetBody):
    requirements = await asyncio.to_thread(requirements_store.list_requirements)
    if not requirements:
        raise HTTPException(status_code=409, detail="No requirements are stored yet")
    tailoring = await get_tailoring_prefix(body.tailoring_prompt_id) + await get_fewshot_prefix()

    # Run per-requirement LLM calls in parallel, bounded concurrency
    # to avoid hitting provider rate limits and ingress proxy timeouts.
    sem = asyncio.Semaphore(8)

    async def run_one(r):
        async with sem:
            return await analyze_one(
                body.provider,
                body.model,
                r["id"],
                r["text"],
                tailoring,
                body.ollama_url,
            )

    # Run the per-requirement scoring and the consistency pass concurrently.
    listing = "\n".join([f"- [{r['id']}] {r['text']}" for r in requirements])

    async def run_consistency():
        try:
            raw = await llm_complete(
                body.provider, body.model, (tailoring or "") + CONSISTENCY_SYSTEM_PROMPT,
                f"Requirements set:\n{listing}",
                body.ollama_url,
            )
            return parse_json_strict(raw)
        except Exception as e:
            logger.exception("consistency failed")
            return {"inconsistencies": [], "error": str(e)}

    results, consistency = await asyncio.gather(
        asyncio.gather(*(run_one(r) for r in requirements)),
        run_consistency(),
    )
    results = list(results)

    # cross-reference per-requirement: tag if appears in any inconsistency
    incon_ids = set()
    for inc in consistency.get("inconsistencies", []):
        for rid in inc.get("requirement_ids", []):
            incon_ids.add(rid)
    for item in results:
        item["has_inconsistency"] = item["requirement_id"] in incon_ids

    avg = round(sum(r.get("overall_score", 0) for r in results) / max(len(results), 1), 1) if results else 0
    return {
        "average_score": avg,
        "results": results,
        "inconsistencies": consistency.get("inconsistencies", []),
        "model": f"{body.provider}/{body.model}",
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


@api.post("/ask")
async def ask_endpoint(body: AskBody):
    requirements = await asyncio.to_thread(requirements_store.list_requirements)
    if not requirements:
        raise HTTPException(status_code=409, detail="No requirements are stored yet")
    listing = "\n".join([f"[{r['id']}] {r['text']}" for r in requirements])
    tailoring = await get_tailoring_prefix(body.tailoring_prompt_id)
    sys_msg = (tailoring or "") + SUMMARIZER_SYSTEM_PROMPT + "\n\nRequirements set:\n" + listing
    try:
        # Use a fresh session each call but keep history client-side
        history = body.history or []
        # send conversation as a single rolled message preceded by history snippet
        if history:
            conv = "\n".join([f"{m.get('role','user')}: {m.get('content','')}" for m in history])
            user_text = f"Previous conversation:\n{conv}\n\nNew question:\n{body.question}"
        else:
            user_text = body.question
        reply = await llm_complete(body.provider, body.model, sys_msg, user_text, body.ollama_url)
        return {"answer": reply}
    except Exception as e:
        logger.exception("summarize_ask failed")
        raise HTTPException(status_code=500, detail=str(e))


# ----- Classifier -----

CLASSIFIER_BASE_INSTRUCTION = """You are an AI requirements classifier.
You will receive a set of engineering requirements. For EACH requirement assign exactly one primary category from the list of allowed categories provided in the system prompt below.
You may also list up to two secondary categories if relevant.

Return ONLY a strict JSON object in this exact shape — no markdown, no commentary:
{
  "results": [
    {
      "requirement_id": "<id>",
      "primary_category": "<one of the allowed categories>",
      "secondary_categories": ["<optional>", "<optional>"],
      "confidence": <integer 0-100>,
      "rationale": "<one-sentence justification>"
    }
  ]
}
"""


@api.post("/classify")
async def classify_endpoint(body: ClassifyBody):
    requirements = await asyncio.to_thread(requirements_store.list_requirements)
    if not requirements:
        raise HTTPException(status_code=409, detail="No requirements are stored yet")

    user_system_prompt = (body.system_prompt or "").strip()
    categories = list(body.categories or [])
    if body.prompt_id:
        p = await db.system_prompts.find_one({"id": body.prompt_id}, {"_id": 0})
        if not p:
            raise HTTPException(status_code=404, detail="Prompt not found")
        if p.get("kind") != "classifier":
            raise HTTPException(status_code=400, detail="Prompt is not a classifier prompt")
        user_system_prompt = p.get("system_prompt", "")
        categories = p.get("categories", []) or categories
    if not user_system_prompt:
        raise HTTPException(status_code=400, detail="A classifier system prompt is required (prompt_id or system_prompt)")

    cats_line = ", ".join(categories) if categories else "(infer reasonable categories from the system prompt)"
    sys_msg = (
        f"{user_system_prompt}\n\n"
        f"ALLOWED CATEGORIES: {cats_line}\n\n"
        f"{CLASSIFIER_BASE_INSTRUCTION}"
    )
    listing = "\n".join([f"- [{r['id']}] {r['text']}" for r in requirements])
    try:
        raw = await llm_complete(
            body.provider,
            body.model,
            sys_msg,
            f"Requirements:\n{listing}",
            body.ollama_url,
        )
        parsed = parse_json_strict(raw)
        results = parsed.get("results", [])
    except Exception as e:
        logger.exception("classify_set failed")
        raise HTTPException(status_code=500, detail=f"Classification failed: {e}")

    # join with requirement text
    by_id = {r["id"]: r for r in requirements}
    enriched = []
    for r in results:
        rid = r.get("requirement_id")
        enriched.append({
            **r,
            "requirement_text": (by_id.get(rid) or {}).get("text", ""),
        })

    # category distribution
    dist: Dict[str, int] = {}
    for r in enriched:
        cat = r.get("primary_category") or "Unclassified"
        dist[cat] = dist.get(cat, 0) + 1

    return {
        "categories": categories,
        "results": enriched,
        "distribution": dist,
        "model": f"{body.provider}/{body.model}",
        "classified_at": datetime.now(timezone.utc).isoformat(),
    }


# ----- System Prompts library (tailoring & classifier) -----

TAILORING_GENERATOR_INSTRUCTION = """You are an expert systems engineer and prompt engineer.
The user will describe their project / domain / standard. Produce a CONCISE system prompt (3-8 sentences) that another LLM can prepend when evaluating requirements for this specific project.
Capture: domain, criticality level (safety, security), key standards / regulations to honour, terminology preferences, and any verification expectations.
Return ONLY valid JSON, no markdown:
{
  "name": "<short 2-5 word title for this tailoring>",
  "description": "<one-sentence description>",
  "system_prompt": "<the prompt content>"
}
"""

CLASSIFIER_GENERATOR_INSTRUCTION = """You are an expert systems engineer.
The user will describe their project and the kinds of categories / departments they want to classify requirements into. Produce:
1. A short system prompt that frames the classification task for this project.
2. A list of 4-10 clear, mutually-exclusive category names.

If the user gave a categories hint, refine and use those names. Otherwise infer sensible ones for the domain (e.g., Functional, Performance, Safety, Security, Usability, Interface, Regulatory, etc.).

Return ONLY valid JSON, no markdown:
{
  "name": "<short 2-5 word title>",
  "description": "<one-sentence description>",
  "system_prompt": "<the framing system prompt>",
  "categories": ["<category 1>", "<category 2>", ...]
}
"""


@api.post("/prompts", response_model=SystemPrompt)
async def create_prompt(body: SystemPromptCreate):
    if body.kind not in ("classifier", "tailoring"):
        raise HTTPException(status_code=400, detail="kind must be 'classifier' or 'tailoring'")
    p = SystemPrompt(**body.model_dump())
    await db.system_prompts.insert_one(p.model_dump())
    return p


@api.get("/prompts")
async def list_prompts(kind: Optional[str] = None):
    q: Dict[str, Any] = {}
    if kind:
        q["kind"] = kind
    cursor = db.system_prompts.find(q, {"_id": 0}).sort("created_at", -1)
    return await cursor.to_list(500)


@api.get("/prompts/{prompt_id}")
async def get_prompt(prompt_id: str):
    doc = await db.system_prompts.find_one({"id": prompt_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return doc


@api.delete("/prompts/{prompt_id}")
async def delete_prompt(prompt_id: str):
    res = await db.system_prompts.delete_one({"id": prompt_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}


@api.post("/prompts/generate")
async def generate_prompt(body: PromptGenerateBody):
    if body.kind not in ("classifier", "tailoring"):
        raise HTTPException(status_code=400, detail="kind must be 'classifier' or 'tailoring'")
    if not body.project_description.strip():
        raise HTTPException(status_code=400, detail="project_description is required")
    if body.kind == "classifier":
        sys_msg = CLASSIFIER_GENERATOR_INSTRUCTION
        hint = ""
        if body.categories_hint:
            hint = "\nCategories hint from user: " + ", ".join(body.categories_hint)
        user_text = f"Project description:\n{body.project_description}{hint}"
    else:
        sys_msg = TAILORING_GENERATOR_INSTRUCTION
        user_text = f"Project description:\n{body.project_description}"
    try:
        raw = await llm_complete(body.provider, body.model, sys_msg, user_text, body.ollama_url)
        parsed = parse_json_strict(raw)
    except Exception as e:
        logger.exception("generate_prompt failed")
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "kind": body.kind,
        "name": parsed.get("name", "Generated prompt"),
        "description": parsed.get("description", ""),
        "system_prompt": parsed.get("system_prompt", ""),
        "categories": parsed.get("categories", []) if body.kind == "classifier" else [],
    }


# ----- Corpus examples (SPEC.md Tier 1: curated good/bad example corpus) -----

@api.post("/corpus/examples", response_model=TrainingExample)
async def create_example(body: TrainingExampleCreate):
    if body.label not in ("good", "bad"):
        raise HTTPException(status_code=400, detail="label must be 'good' or 'bad'")
    ex = TrainingExample(**body.model_dump())
    await db.training_examples.insert_one(ex.model_dump())
    return ex


@api.get("/corpus/examples")
async def list_examples():
    cursor = db.training_examples.find({}, {"_id": 0}).sort("created_at", -1)
    return await cursor.to_list(1000)


@api.delete("/corpus/examples/{ex_id}")
async def delete_example(ex_id: str):
    res = await db.training_examples.delete_one({"id": ex_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}


@api.post("/training/examples", response_model=TrainingExample, deprecated=True)
async def create_example_legacy(body: TrainingExampleCreate):
    return await create_example(body)


@api.get("/training/examples", deprecated=True)
async def list_examples_legacy():
    return await list_examples()


@api.delete("/training/examples/{ex_id}", deprecated=True)
async def delete_example_legacy(ex_id: str):
    return await delete_example(ex_id)


# ----- Training datasets -----

def parse_dataset_file(filename: str, content: bytes) -> List[Dict[str, Any]]:
    name = (filename or "").lower()
    text = content.decode("utf-8", errors="ignore")
    rows: List[Dict[str, Any]] = []
    if name.endswith(".jsonl"):
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    elif name.endswith(".json"):
        data = json.loads(text)
        if isinstance(data, list):
            rows = data
        else:
            raise ValueError("JSON dataset must be a list")
    else:
        # CSV with columns prompt,completion or system,user,assistant
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            rows.append(row)
    if not rows:
        raise ValueError("Empty dataset")
    return rows


@api.post("/training/datasets")
async def upload_dataset(file: UploadFile = File(...), name: Optional[str] = Form(None)):
    content = await file.read()
    try:
        rows = parse_dataset_file(file.filename, content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse dataset: {e}")
    ds = TrainingDataset(name=name or file.filename, sample_count=len(rows), samples=rows)
    await db.training_datasets.insert_one(ds.model_dump())
    return {"id": ds.id, "name": ds.name, "sample_count": ds.sample_count, "created_at": ds.created_at}


@api.get("/training/datasets")
async def list_datasets():
    cursor = db.training_datasets.find({}, {"_id": 0, "samples": 0}).sort("created_at", -1)
    return await cursor.to_list(200)


@api.get("/training/datasets/{ds_id}")
async def get_dataset(ds_id: str):
    doc = await db.training_datasets.find_one({"id": ds_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return doc


@api.delete("/training/datasets/{ds_id}")
async def delete_dataset(ds_id: str):
    res = await db.training_datasets.delete_one({"id": ds_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}


# ----- Distillation (OpenAI fine-tuning) -----

def samples_to_jsonl_bytes(samples: List[Dict[str, Any]]) -> bytes:
    """Convert dataset samples to OpenAI chat fine-tune JSONL: {"messages":[...]}."""
    lines: List[str] = []
    for row in samples:
        if "messages" in row and isinstance(row["messages"], list):
            lines.append(json.dumps({"messages": row["messages"]}))
            continue
        # prompt/completion -> chat format
        sys_msg = row.get("system") or "You are a helpful assistant."
        user = row.get("user") or row.get("prompt") or row.get("input") or ""
        assistant = row.get("assistant") or row.get("completion") or row.get("output") or ""
        if not user or not assistant:
            continue
        lines.append(json.dumps({
            "messages": [
                {"role": "system", "content": str(sys_msg)},
                {"role": "user", "content": str(user)},
                {"role": "assistant", "content": str(assistant)},
            ]
        }))
    if not lines:
        raise ValueError("Dataset did not contain valid samples (needs messages OR prompt/completion fields).")
    return ("\n".join(lines)).encode("utf-8")


@api.post("/distillation/jobs")
async def start_distillation(body: DistillationStartBody):
    ds = await db.training_datasets.find_one({"id": body.dataset_id}, {"_id": 0})
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    try:
        jsonl_bytes = samples_to_jsonl_bytes(ds.get("samples", []))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    job = DistillationJob(
        name=body.name,
        base_model=body.base_model,
        dataset_id=body.dataset_id,
        dataset_name=ds.get("name"),
        status="uploading_file",
        hyperparameters={"n_epochs": body.n_epochs} if body.n_epochs else {},
    )

    headers = {"Authorization": f"Bearer {body.openai_api_key}"}

    # 1) Upload file
    try:
        files = {"file": ("training.jsonl", jsonl_bytes, "application/jsonl")}
        data = {"purpose": "fine-tune"}
        r = requests.post("https://api.openai.com/v1/files", headers=headers, files=files, data=data, timeout=60)
        if r.status_code >= 300:
            job.status = "failed"
            job.error = f"file upload: {r.status_code} {r.text}"
            await db.distillation_jobs.insert_one(job.model_dump())
            raise HTTPException(status_code=400, detail=job.error)
        file_id = r.json().get("id")
        job.openai_file_id = file_id
    except HTTPException:
        raise
    except Exception as e:
        job.status = "failed"
        job.error = f"file upload error: {e}"
        await db.distillation_jobs.insert_one(job.model_dump())
        raise HTTPException(status_code=500, detail=job.error)

    # 2) Create fine-tuning job
    payload: Dict[str, Any] = {"training_file": file_id, "model": body.base_model}
    if body.n_epochs:
        payload["hyperparameters"] = {"n_epochs": body.n_epochs}
    try:
        r = requests.post(
            "https://api.openai.com/v1/fine_tuning/jobs",
            headers={**headers, "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
        if r.status_code >= 300:
            job.status = "failed"
            job.error = f"create job: {r.status_code} {r.text}"
            await db.distillation_jobs.insert_one(job.model_dump())
            raise HTTPException(status_code=400, detail=job.error)
        body_json = r.json()
        job.openai_job_id = body_json.get("id")
        job.status = body_json.get("status", "queued")
    except HTTPException:
        raise
    except Exception as e:
        job.status = "failed"
        job.error = f"create job error: {e}"
        await db.distillation_jobs.insert_one(job.model_dump())
        raise HTTPException(status_code=500, detail=job.error)

    await db.distillation_jobs.insert_one(job.model_dump())
    return job


@api.get("/distillation/jobs")
async def list_jobs():
    cursor = db.distillation_jobs.find({}, {"_id": 0}).sort("created_at", -1)
    return await cursor.to_list(200)


@api.post("/distillation/jobs/{job_id}/refresh")
async def refresh_job(job_id: str, openai_api_key: str = Form(...)):
    job = await db.distillation_jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.get("openai_job_id"):
        return job
    try:
        r = requests.get(
            f"https://api.openai.com/v1/fine_tuning/jobs/{job['openai_job_id']}",
            headers={"Authorization": f"Bearer {openai_api_key}"},
            timeout=30,
        )
        if r.status_code >= 300:
            raise HTTPException(status_code=400, detail=f"{r.status_code} {r.text}")
        info = r.json()
        await db.distillation_jobs.update_one(
            {"id": job_id},
            {"$set": {
                "status": info.get("status", job.get("status")),
                "fine_tuned_model": info.get("fine_tuned_model"),
                "error": (info.get("error") or {}).get("message") if info.get("error") else None,
            }},
        )
        job = await db.distillation_jobs.find_one({"id": job_id}, {"_id": 0})
        return job
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api.delete("/distillation/jobs/{job_id}")
async def delete_job(job_id: str):
    res = await db.distillation_jobs.delete_one({"id": job_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}


# ----- Training tier 3 scaffolding: dataset export, model registry, evaluate -----
# SPEC.md Phase 5. The engine does not fine-tune; it produces the export and
# consumes/evaluates whatever model comes back from an external LoRA run.

class RegistryUpdateBody(BaseModel):
    provider: str
    model: str


class EvaluateBody(BaseModel):
    feature: str = "review"
    candidate_provider: str
    candidate_model: str
    example_ids: Optional[List[str]] = None
    tailoring_prompt_id: Optional[str] = None
    ollama_url: Optional[str] = None


# ----- Test generation: project test context (SPEC-ADDENDUM-A, Stage A) -----
# Context analysis + elicitation only. Category-aware generation, the two-verdict
# sufficiency gate, and the resolve/regenerate loop (addendum A.3-A.5) are later
# stages, not implemented yet.

class TestContextItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: str
    key: str
    value: str
    source_requirement_ids: List[str] = []
    status: str = "inferred"  # inferred | confirmed | user_provided


class TestContextQuestion(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question: str
    reason: str
    status: str = "open"  # open | answered
    answer: Optional[str] = None


class TestContext(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    version: int
    items: List[TestContextItem] = []
    questions: List[TestContextQuestion] = []
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ContextAnalyzeBody(BaseModel):
    provider: str = "ollama"
    model: str = "gemma3:1b"
    ollama_url: Optional[str] = None


class ContextItemUpdate(BaseModel):
    id: str
    value: str


class ContextNewItem(BaseModel):
    category: str
    key: str
    value: str
    source_requirement_ids: List[str] = []


class ContextQuestionAnswer(BaseModel):
    question_id: str
    answer: str


class ContextPatchBody(BaseModel):
    item_updates: List[ContextItemUpdate] = []
    new_items: List[ContextNewItem] = []
    question_answers: List[ContextQuestionAnswer] = []
    item_removals: List[str] = []


async def get_current_test_context() -> Optional[Dict[str, Any]]:
    cursor = db.test_context_versions.find({}, {"_id": 0}).sort("version", -1)
    docs = await cursor.to_list(1)
    return docs[0] if docs else None


# ----- Test generation: category strategies + generation (SPEC-ADDENDUM-A, Stages B+C) -----

class CategoryStrategyBody(BaseModel):
    instructions: str


class GenerateBody(BaseModel):
    requirement_ids: Optional[List[str]] = None
    provider: str = "ollama"
    model: str = "gemma3:1b"
    ollama_url: Optional[str] = None


class TestCase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    requirement_ids: List[str]
    category: str
    category_source: str = "engine-assigned"
    preconditions: List[str] = []
    steps: List[str] = []
    acceptance_criteria: List[str] = []
    verification_method: str = "test"
    review_flags: List[str] = []
    assumptions: List[Dict[str, Any]] = []
    context_version: Optional[int] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


async def get_category_strategies() -> Dict[str, str]:
    strategies = dict(DEFAULT_CATEGORY_STRATEGIES)
    cursor = db.category_strategies.find({}, {"_id": 0})
    for doc in await cursor.to_list(500):
        strategies[doc["category"]] = doc["instructions"]
    return strategies


@api.get("/testgen/category-strategies")
async def list_category_strategies():
    strategies = await get_category_strategies()
    return [{"category": category, "instructions": instructions} for category, instructions in strategies.items()]


@api.put("/testgen/category-strategies/{category}")
async def set_category_strategy(category: str, body: CategoryStrategyBody):
    existing = await db.category_strategies.find_one({"category": category})
    entry = {"category": category, "instructions": body.instructions}
    if existing:
        await db.category_strategies.update_one({"category": category}, {"$set": entry})
    else:
        await db.category_strategies.insert_one(entry)
    return entry


@api.delete("/testgen/category-strategies/{category}")
async def delete_category_strategy(category: str):
    res = await db.category_strategies.delete_one({"category": category})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="No stored override for this category")
    return {"deleted": True}


def _context_listing(context: Optional[Dict[str, Any]]) -> str:
    if not context or not context.get("items"):
        return "(no project test context yet)"
    return "\n".join(f"[{item['category']}] {item['key']}: {item['value']}" for item in context["items"])


async def _persist_gaps(requirement_id: str, gaps: List[Dict[str, Any]], gap_source: str) -> List[Dict[str, Any]]:
    persisted = []
    for gap in gaps:
        doc = {
            "id": str(uuid.uuid4()),
            "requirement_id": requirement_id,
            "item": str(gap.get("item", "")),
            "why": str(gap.get("why", "")),
            "gap_source": gap_source,
            "status": "open",
            "resolution_options": ["answer", "authorize_fill"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.test_gaps.insert_one(doc)
        persisted.append(
            {
                "gap_id": doc["id"],
                "item": doc["item"],
                "why": doc["why"],
                "gap_source": doc["gap_source"],
                "resolution_options": doc["resolution_options"],
            }
        )
    return persisted


async def _generate_for_requirement(
    requirement: Dict[str, Any],
    strategies: Dict[str, str],
    context: Optional[Dict[str, Any]],
    provider: str,
    model: str,
    ollama_url: Optional[str],
    extra_assumptions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    req_id = requirement["id"]
    req_text = requirement["text"]
    extra_assumptions = extra_assumptions or []
    context_listing = _context_listing(context)
    context_version = context["version"] if context else None
    category_listing = "\n".join(f"- {name}: {instructions}" for name, instructions in strategies.items())
    assumption_listing = "\n".join(
        f"- {a['text']}: {a['value']} ({a['rationale']})" for a in extra_assumptions
    )

    assess_user_message = (
        f"Requirement: [{req_id}] {req_text}\n\n"
        f"Known categories:\n{category_listing}\n\n"
        f"Project test context:\n{context_listing}"
    )
    if assumption_listing:
        assess_user_message += f"\n\nAuthorized assumptions for this requirement:\n{assumption_listing}"

    try:
        raw = await llm_complete(provider, model, CLASSIFY_AND_ASSESS_PROMPT, assess_user_message, ollama_url)
        assessment = extract_json(raw)
        category = str(assessment.get("category", "")).strip() or "Functional"
        sufficient = bool(assessment.get("sufficient"))
        gaps = assessment.get("gaps", []) or []
    except Exception as exc:
        logger.exception("testgen classify/assess failed")
        gap_dicts = await _persist_gaps(req_id, [{"item": "analysis failed", "why": str(exc)}], "sufficiency")
        return {"requirement_id": req_id, "status": "needs_input", "category": None, "gaps": gap_dicts}

    if not sufficient:
        gap_dicts = await _persist_gaps(req_id, gaps, "sufficiency")
        return {"requirement_id": req_id, "status": "needs_input", "category": category, "gaps": gap_dicts}

    strategy_instructions = strategies.get(category, "Use sound test-engineering judgement for this category.")
    generate_user_message = (
        f"Requirement: [{req_id}] {req_text}\n\n"
        f"Category: {category}\n"
        f"Category strategy: {strategy_instructions}\n\n"
        f"Project test context:\n{context_listing}"
    )
    if assumption_listing:
        generate_user_message += f"\n\nAuthorized assumptions for this requirement:\n{assumption_listing}"

    try:
        raw = await llm_complete(provider, model, GENERATE_TEST_CASE_PROMPT, generate_user_message, ollama_url)
        generated = extract_json(raw)
    except Exception as exc:
        logger.exception("testgen generation failed")
        gap_dicts = await _persist_gaps(req_id, [{"item": "generation failed", "why": str(exc)}], "sufficiency")
        return {"requirement_id": req_id, "status": "needs_input", "category": category, "gaps": gap_dicts}

    context_items = context["items"] if context else []
    violations = check_anti_genericity(generated, req_text, context_items, extra_assumptions)
    if violations:
        gap_dicts = await _persist_gaps(req_id, violations, "self_review")
        return {"requirement_id": req_id, "status": "needs_input", "category": category, "gaps": gap_dicts}

    test_case = TestCase(
        requirement_ids=[req_id],
        category=category,
        preconditions=[str(x) for x in generated.get("preconditions", []) or []],
        steps=[str(x) for x in generated.get("steps", []) or []],
        acceptance_criteria=[str(x) for x in generated.get("acceptance_criteria", []) or []],
        verification_method=str(generated.get("verification_method", "test")),
        review_flags=["safety"] if category == "Safety-related" else [],
        assumptions=extra_assumptions,
        context_version=context_version,
    )
    await db.test_cases.insert_one(test_case.model_dump())
    return {"requirement_id": req_id, "status": "generated", "category": category, "test_case": test_case}


@api.post("/testgen/generate")
async def generate_test_cases(body: GenerateBody):
    all_requirements = await asyncio.to_thread(requirements_store.list_requirements)
    if body.requirement_ids is not None:
        wanted = set(body.requirement_ids)
        requirements = [r for r in all_requirements if r["id"] in wanted]
    else:
        requirements = all_requirements
    if not requirements:
        raise HTTPException(status_code=400, detail="No matching requirements to generate for")

    strategies = await get_category_strategies()
    context = await get_current_test_context()
    sem = asyncio.Semaphore(8)

    async def process(requirement: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            return await _generate_for_requirement(
                requirement, strategies, context, body.provider, body.model, body.ollama_url
            )

    results = await asyncio.gather(*(process(r) for r in requirements))
    return {"results": results}


@api.get("/testgen/testcases")
async def list_test_cases(requirement_id: Optional[str] = None):
    cursor = db.test_cases.find({}, {"_id": 0}).sort("created_at", -1)
    docs = await cursor.to_list(1000)
    if requirement_id:
        docs = [doc for doc in docs if requirement_id in doc.get("requirement_ids", [])]
    return docs


# ----- Test generation: resolve/regenerate loop (SPEC-ADDENDUM-A, Stage C) -----

class ResolveBody(BaseModel):
    gap_id: str
    resolution_type: str  # "answer" | "authorize_fill"
    answer: Optional[str] = None
    provider: str = "ollama"
    model: str = "gemma3:1b"
    ollama_url: Optional[str] = None


@api.post("/testgen/resolve")
async def resolve_gap(body: ResolveBody):
    gap = await db.test_gaps.find_one({"id": body.gap_id})
    if not gap:
        raise HTTPException(status_code=404, detail=f"Unknown gap id: {body.gap_id}")
    if gap["status"] != "open":
        raise HTTPException(status_code=409, detail="This gap has already been resolved")

    all_requirements = await asyncio.to_thread(requirements_store.list_requirements)
    requirement = next((r for r in all_requirements if r["id"] == gap["requirement_id"]), None)
    if not requirement:
        raise HTTPException(status_code=404, detail=f"Requirement no longer exists: {gap['requirement_id']}")

    strategies = await get_category_strategies()
    extra_assumptions: List[Dict[str, Any]] = []

    if body.resolution_type == "answer":
        if not body.answer:
            raise HTTPException(status_code=400, detail="'answer' is required for resolution_type=answer")
        current = await get_current_test_context()
        items = [dict(item) for item in current["items"]] if current else []
        items.append(
            TestContextItem(
                category="elicited",
                key=gap["item"],
                value=body.answer,
                status="user_provided",
            ).model_dump()
        )
        next_version = (current["version"] + 1) if current else 1
        questions = current["questions"] if current else []
        new_context = TestContext(version=next_version, items=items, questions=questions)
        await db.test_context_versions.insert_one(new_context.model_dump())
        context = new_context.model_dump()
    elif body.resolution_type == "authorize_fill":
        fill_user_message = (
            f"Requirement: [{requirement['id']}] {requirement['text']}\n\n"
            f"Missing item: {gap['item']}\n"
            f"Why it's needed: {gap['why']}"
        )
        try:
            raw = await llm_complete(body.provider, body.model, AUTHORIZE_FILL_PROMPT, fill_user_message, body.ollama_url)
            proposal = extract_json(raw)
            value = str(proposal.get("value", "")).strip()
            if not value:
                raise ValueError("empty proposed value")
            extra_assumptions = [
                {
                    "text": gap["item"],
                    "value": value,
                    "rationale": str(proposal.get("rationale", "")),
                    "confidence": str(proposal.get("confidence", "low")),
                }
            ]
        except Exception as exc:
            logger.exception("testgen authorize_fill proposal failed")
            gap_dicts = await _persist_gaps(
                requirement["id"],
                [{"item": gap["item"], "why": f"Automatic fill-in failed: {exc}"}],
                "sufficiency",
            )
            await db.test_gaps.update_one({"id": body.gap_id}, {"$set": {"status": "resolved"}})
            return {"requirement_id": requirement["id"], "status": "needs_input", "category": None, "gaps": gap_dicts}
        context = await get_current_test_context()
    else:
        raise HTTPException(status_code=400, detail="resolution_type must be 'answer' or 'authorize_fill'")

    await db.test_gaps.update_one({"id": body.gap_id}, {"$set": {"status": "resolved"}})

    return await _generate_for_requirement(
        requirement, strategies, context, body.provider, body.model, body.ollama_url, extra_assumptions=extra_assumptions
    )


@api.get("/testgen/gaps")
async def list_gaps(status: Optional[str] = None):
    cursor = db.test_gaps.find({}, {"_id": 0}).sort("created_at", -1)
    docs = await cursor.to_list(1000)
    if status:
        docs = [doc for doc in docs if doc.get("status") == status]
    return docs


@api.delete("/testgen/gaps/{gap_id}")
async def dismiss_gap(gap_id: str):
    # Distinct from "resolved" (which /testgen/resolve sets after a real answer or
    # authorize_fill regenerates through the gap) - a dismissed gap was never actually
    # addressed, just acknowledged as not worth chasing further. Kept as its own status
    # rather than deleted so the gap's history stays honest.
    gap = await db.test_gaps.find_one({"id": gap_id})
    if not gap:
        raise HTTPException(status_code=404, detail=f"Unknown gap id: {gap_id}")
    if gap["status"] != "open":
        raise HTTPException(status_code=409, detail="This gap is not open")
    await db.test_gaps.update_one({"id": gap_id}, {"$set": {"status": "dismissed"}})
    return {"dismissed": True}


@api.post("/datasets/export")
async def export_dataset(label: Optional[str] = None):
    query = {"label": label} if label else {}
    cursor = db.training_examples.find(query, {"_id": 0})
    examples = await cursor.to_list(10000)
    if not examples:
        raise HTTPException(status_code=400, detail="No corpus examples to export")

    lines = [
        json.dumps(model_registry.build_training_row(example, INDIVIDUAL_SYSTEM_PROMPT))
        for example in examples
    ]
    content = ("\n".join(lines) + "\n").encode("utf-8")
    return Response(
        content=content,
        media_type="application/jsonl",
        headers={"Content-Disposition": "attachment; filename=corpus_export.jsonl"},
    )


@api.get("/models/registry")
async def get_model_registry():
    entries = []
    for feature in model_registry.FEATURES:
        doc = await db.model_registry.find_one({"id": feature}, {"_id": 0})
        if doc:
            entries.append(doc)
        else:
            entries.append({"id": feature, **model_registry.default_entry(), "updated_at": None})
    return entries


@api.put("/models/registry/{feature}")
async def set_model_registry_entry(feature: str, body: RegistryUpdateBody):
    if feature not in model_registry.FEATURES:
        raise HTTPException(status_code=400, detail=f"Unknown feature. Expected one of {model_registry.FEATURES}")
    entry = {
        "id": feature,
        "provider": body.provider,
        "model": body.model,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    existing = await db.model_registry.find_one({"id": feature})
    if existing:
        await db.model_registry.update_one({"id": feature}, {"$set": entry})
    else:
        await db.model_registry.insert_one(entry)
    return entry


async def get_active_model(feature: str) -> tuple[str, str]:
    doc = await db.model_registry.find_one({"id": feature}, {"_id": 0})
    if doc:
        return doc["provider"], doc["model"]
    default = model_registry.default_entry()
    return default["provider"], default["model"]


@api.post("/models/evaluate")
async def evaluate_model(body: EvaluateBody):
    if body.feature != "review":
        raise HTTPException(
            status_code=400,
            detail="Only the 'review' feature is currently supported for evaluation",
        )

    if body.example_ids is not None:
        examples = []
        for ex_id in body.example_ids:
            doc = await db.training_examples.find_one({"id": ex_id}, {"_id": 0})
            if doc:
                examples.append(doc)
    else:
        cursor = db.training_examples.find({}, {"_id": 0})
        examples = await cursor.to_list(10000)
    if not examples:
        raise HTTPException(status_code=400, detail="No corpus examples available to evaluate against")

    baseline_provider, baseline_model = await get_active_model(body.feature)
    tailoring = await get_tailoring_prefix(body.tailoring_prompt_id)

    sem = asyncio.Semaphore(8)

    async def run(provider: str, model: str, example: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            result = await analyze_one(
                provider,
                model,
                f"EX-{example['id'][:8]}",
                example["requirement_text"],
                tailoring,
                body.ollama_url,
            )
            return {
                "example_id": example["id"],
                "label": example.get("label"),
                "score": result.get("overall_score", 0),
                "degraded": bool(result.get("fallback")),
            }

    baseline_results, candidate_results = await asyncio.gather(
        asyncio.gather(*(run(baseline_provider, baseline_model, ex) for ex in examples)),
        asyncio.gather(*(run(body.candidate_provider, body.candidate_model, ex) for ex in examples)),
    )

    def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
        correct = sum(
            1 for r in results if model_registry.score_matches_expectation(r["label"], r["score"])
        )
        return {
            "average_score": round(sum(r["score"] for r in results) / len(results), 1),
            "accuracy": round(correct / len(results), 3),
            "degraded_count": sum(1 for r in results if r["degraded"]),
        }

    detail = [
        {
            "example_id": b["example_id"],
            "label": b["label"],
            "baseline_score": b["score"],
            "candidate_score": c["score"],
        }
        for b, c in zip(baseline_results, candidate_results)
    ]

    return {
        "feature": body.feature,
        "baseline": {"provider": baseline_provider, "model": baseline_model, **summarize(baseline_results)},
        "candidate": {
            "provider": body.candidate_provider,
            "model": body.candidate_model,
            **summarize(candidate_results),
        },
        "detail": detail,
    }


@api.post("/testgen/context/analyze")
async def analyze_test_context(body: ContextAnalyzeBody):
    requirements = await asyncio.to_thread(requirements_store.list_requirements)
    if not requirements:
        raise HTTPException(status_code=400, detail="No requirements are stored yet")
    reference_chunks = await asyncio.to_thread(requirements_store.list_reference_chunks)

    listing = "\n".join(f"- [{r['id']}] {r['text']}" for r in requirements)
    if reference_chunks:
        listing += "\n\nReference material:\n" + "\n".join(
            f"- [{c['document']}] {c['text']}" for c in reference_chunks
        )

    items: List[Dict[str, Any]] = []
    questions: List[Dict[str, Any]] = []
    try:
        raw = await llm_complete(body.provider, body.model, CONTEXT_ANALYSIS_PROMPT, listing, body.ollama_url)
        parsed = extract_json(raw)
        for item in parsed.get("items", []) or []:
            items.append(
                TestContextItem(
                    category=str(item.get("category", "")),
                    key=str(item.get("key", "")),
                    value=str(item.get("value", "")),
                    source_requirement_ids=[str(i) for i in item.get("source_requirement_ids", []) or []],
                ).model_dump()
            )
        for question in parsed.get("questions", []) or []:
            questions.append(
                TestContextQuestion(
                    question=str(question.get("question", "")),
                    reason=str(question.get("reason", "")),
                ).model_dump()
            )
    except Exception as exc:
        logger.exception("test context analysis failed")
        questions.append(
            TestContextQuestion(
                question="Context analysis failed - please retry.",
                reason=f"The analysis LLM call did not return usable output: {exc}",
            ).model_dump()
        )

    previous = await get_current_test_context()
    next_version = (previous["version"] + 1) if previous else 1
    context = TestContext(version=next_version, items=items, questions=questions)
    await db.test_context_versions.insert_one(context.model_dump())
    return context


@api.get("/testgen/context")
async def get_test_context():
    context = await get_current_test_context()
    if not context:
        raise HTTPException(
            status_code=404,
            detail="No project test context yet - run POST /testgen/context/analyze first",
        )
    return context


@api.patch("/testgen/context")
async def patch_test_context(body: ContextPatchBody):
    current = await get_current_test_context()
    if not current:
        raise HTTPException(
            status_code=404,
            detail="No project test context yet - run POST /testgen/context/analyze first",
        )

    items = [dict(item) for item in current["items"]]
    by_id = {item["id"]: item for item in items}
    for update in body.item_updates:
        target = by_id.get(update.id)
        if not target:
            raise HTTPException(status_code=404, detail=f"Unknown context item id: {update.id}")
        target["value"] = update.value
        if target["status"] == "inferred":
            target["status"] = "confirmed"

    for removal_id in body.item_removals:
        if removal_id not in by_id:
            raise HTTPException(status_code=404, detail=f"Unknown context item id: {removal_id}")
    if body.item_removals:
        removed = set(body.item_removals)
        items = [item for item in items if item["id"] not in removed]

    for new_item in body.new_items:
        items.append(
            TestContextItem(
                category=new_item.category,
                key=new_item.key,
                value=new_item.value,
                source_requirement_ids=new_item.source_requirement_ids,
                status="user_provided",
            ).model_dump()
        )

    questions = [dict(question) for question in current["questions"]]
    by_qid = {question["id"]: question for question in questions}
    for answer in body.question_answers:
        target = by_qid.get(answer.question_id)
        if not target:
            raise HTTPException(status_code=404, detail=f"Unknown question id: {answer.question_id}")
        target["status"] = "answered"
        target["answer"] = answer.answer
        items.append(
            TestContextItem(
                category="elicited",
                key=target["question"],
                value=answer.answer,
                status="user_provided",
            ).model_dump()
        )

    context = TestContext(version=current["version"] + 1, items=items, questions=questions)
    await db.test_context_versions.insert_one(context.model_dump())
    return context


@api.get("/testgen/context/questions")
async def get_test_context_questions(status: Optional[str] = None):
    context = await get_current_test_context()
    if not context:
        return []
    questions = context["questions"]
    if status:
        questions = [q for q in questions if q.get("status") == status]
    return questions


# ---------- Mount ----------

requirements_router = create_requirements_router(llm_complete, fetch_fewshot_examples=get_fewshot_prefix)
requirements_store = requirements_router.store
requirements_indexer = requirements_router.indexer

# /api/v1 is the authed, versioned surface. /api is a thin deprecated alias
# (same routers, no auth) kept only until every consumer has switched to v1 —
# see SPEC.md Phase 1.
app.include_router(api, prefix="/api/v1", dependencies=[Depends(require_api_key)])
app.include_router(api, prefix="/api", deprecated=True)

app.include_router(requirements_router, prefix="/api/v1", dependencies=[Depends(require_api_key)])
app.include_router(requirements_router, prefix="/api", deprecated=True)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/testgen-console", include_in_schema=False)
async def testgen_console():
    # Lightweight, dependency-free dev tool for exercising the test-case-generation
    # endpoints without /docs's raw-JSON Swagger forms (no build step, no framework -
    # mirrors the legacy Node prototype's plain-JS approach). Not part of the main
    # React frontend; testgen has no tab there yet.
    return FileResponse(ROOT_DIR / "static" / "testgen_console.html")


frontend_build = ROOT_DIR.parent / "frontend" / "build"
if frontend_build.exists():
    app.mount("/", StaticFiles(directory=frontend_build, html=True), name="frontend")


@app.on_event("shutdown")
async def shutdown_db_client():
    if client is not None:
        client.close()
