"""ReqIQ backend API tests"""
import io
import json
import os
import time

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8000").rstrip("/")
API = f"{BASE_URL}/api/v1"

LLM_TIMEOUT = 90
SET_TIMEOUT = 240


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    return s


# ---------- Models / Rules ----------
def test_get_models(session):
    r = session.get(f"{API}/models", timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    for k in ("openai", "anthropic", "gemini"):
        assert k in data
        assert isinstance(data[k], list) and len(data[k]) > 0
        assert "id" in data[k][0] and "label" in data[k][0]


def test_get_incose_rules(session):
    r = session.get(f"{API}/incose/rules", timeout=20)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    keys = {x["key"] for x in data}
    expected = {"necessary", "singular", "unambiguous", "complete",
                "verifiable", "feasible", "consistent", "traceable"}
    assert expected.issubset(keys)
    assert len(data) == 8


# ---------- Requirements (single unified collection, no more "sets") ----------
@pytest.fixture(scope="module")
def uploaded_set(session):
    sample = [
        {"id": "REQ-001", "text": "The system shall be fast."},
        {"id": "REQ-002", "text": "The system shall respond within 200 ms under nominal load (50 concurrent users)."},
        {"id": "REQ-003", "text": "The system shall support TLS 1.3 for all external traffic and shall reject TLS 1.2 connections."},
        {"id": "REQ-004", "text": "The system shall support TLS 1.2 for all external traffic."},
    ]
    payload = {"requirements": sample}
    r = session.put(f"{API}/requirements", json=payload, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["requirements"]) == 4
    assert body["requirements"][0]["id"] == "REQ-001"
    yield body
    # cleanup: reset to an empty collection
    session.put(f"{API}/requirements", json={"requirements": []}, timeout=20)


def test_list_requirements(session, uploaded_set):
    r = session.get(f"{API}/requirements", timeout=20)
    assert r.status_code == 200
    body = r.json()
    ids = [i["id"] for i in body["requirements"]]
    assert "REQ-001" in ids


# ---------- Individual analyze ----------
def test_analyze_individual_ambiguous(session):
    payload = {
        "text": "The system shall be fast and easy to use.",
        "requirement_id": "REQ-TEST-1",
        "provider": "openai",
        "model": "gpt-4o-mini",
    }
    r = session.post(f"{API}/review/requirement", json=payload, timeout=LLM_TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "error" not in data, f"LLM analysis errored: {data.get('error')}"
    assert isinstance(data.get("overall_score"), int)
    assert isinstance(data.get("summary"), str) and len(data["summary"]) > 0
    assert isinstance(data.get("proposed_fix"), str)
    rules = data.get("rules", {})
    expected = {"necessary", "singular", "unambiguous", "complete",
                "verifiable", "feasible", "consistent", "traceable"}
    assert expected.issubset(set(rules.keys()))
    for k in expected:
        assert "score" in rules[k]
        assert "finding" in rules[k]


# ---------- Set-level review ----------
def test_review_set(session, uploaded_set):
    payload = {"provider": "openai", "model": "gpt-4o-mini"}
    r = session.post(f"{API}/review/set", json=payload, timeout=SET_TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "average_score" in data
    assert isinstance(data["results"], list) and len(data["results"]) == 4
    for item in data["results"]:
        assert "overall_score" in item
        assert "rules" in item
    assert "inconsistencies" in data
    assert isinstance(data["inconsistencies"], list)


# ---------- Ask ----------
def test_ask(session, uploaded_set):
    payload = {
        "question": "Summarise the security-related requirements",
        "provider": "openai",
        "model": "gpt-4o-mini",
    }
    r = session.post(f"{API}/ask", json=payload, timeout=LLM_TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data.get("answer"), str)
    assert len(data["answer"]) > 0


# ---------- Training examples ----------
def test_training_examples_crud(session):
    payload = {
        "label": "good",
        "requirement_text": "TEST_example: The system shall respond within 200 ms.",
        "explanation": "verifiable, measurable",
        "corrected_text": "",
    }
    r = session.post(f"{API}/training/examples", json=payload, timeout=20)
    assert r.status_code == 200, r.text
    ex = r.json()
    assert ex["label"] == "good"
    ex_id = ex["id"]

    # list
    r = session.get(f"{API}/training/examples", timeout=20)
    assert r.status_code == 200
    ids = [e["id"] for e in r.json()]
    assert ex_id in ids

    # delete
    r = session.delete(f"{API}/training/examples/{ex_id}", timeout=20)
    assert r.status_code == 200
    assert r.json().get("deleted") is True


def test_training_example_invalid_label(session):
    payload = {"label": "neutral", "requirement_text": "x"}
    r = session.post(f"{API}/training/examples", json=payload, timeout=20)
    assert r.status_code == 400


# ---------- Training datasets ----------
@pytest.fixture
def dataset(session):
    rows = [
        {"messages": [
            {"role": "system", "content": "You are an INCOSE rewriter."},
            {"role": "user", "content": "The system shall be fast."},
            {"role": "assistant", "content": "The system shall respond within 200 ms (P95) under nominal load."},
        ]},
        {"messages": [
            {"role": "system", "content": "You are an INCOSE rewriter."},
            {"role": "user", "content": "Easy to use."},
            {"role": "assistant", "content": "The system shall provide a UI usable by a trained operator within 5 minutes."},
        ]},
    ]
    content = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
    files = {"file": ("TEST_ds.jsonl", io.BytesIO(content), "application/jsonl")}
    data = {"name": "TEST_dataset"}
    r = session.post(f"{API}/training/datasets", files=files, data=data, timeout=20)
    assert r.status_code == 200, r.text
    ds = r.json()
    yield ds
    session.delete(f"{API}/training/datasets/{ds['id']}", timeout=20)


def test_dataset_crud(session, dataset):
    assert dataset["sample_count"] == 2
    r = session.get(f"{API}/training/datasets", timeout=20)
    assert r.status_code == 200
    ids = [d["id"] for d in r.json()]
    assert dataset["id"] in ids


# ---------- Distillation (invalid key path) ----------
def test_distillation_invalid_key(session, dataset):
    payload = {
        "name": "TEST_distill_invalid",
        "dataset_id": dataset["id"],
        "base_model": "gpt-4o-mini-2024-07-18",
        "openai_api_key": "sk-invalid-test",
        "n_epochs": 1,
    }
    r = session.post(f"{API}/distillation/jobs", json=payload, timeout=60)
    assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
    detail = r.json().get("detail", "")
    assert "file upload" in detail.lower() or "invalid" in detail.lower() or "401" in detail or "incorrect api key" in detail.lower()
