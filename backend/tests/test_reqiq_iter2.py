"""ReqIQ iteration-2 tests: prompts library (tailoring + classifier), prompt generator, classify/set, tailored analyze."""
import io
import json
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"

LLM_TIMEOUT = 120
SET_TIMEOUT = 240
PROVIDER = "openai"
MODEL = "gpt-4o-mini"


@pytest.fixture(scope="module")
def session():
    return requests.Session()


# ---------- Sample set used by classify + tailored analyze tests ----------
@pytest.fixture(scope="module")
def uploaded_set(session):
    sample = [
        {"id": "REQ-001", "text": "The system shall encrypt user passwords with bcrypt cost factor 12."},
        {"id": "REQ-002", "text": "The system shall respond within 200 ms under nominal load (50 concurrent users)."},
        {"id": "REQ-003", "text": "The login page shall provide a 'Forgot password' link."},
        {"id": "REQ-004", "text": "The system shall log all authentication failures to the audit log."},
    ]
    fd = io.BytesIO(json.dumps(sample).encode("utf-8"))
    files = {"file": ("TEST_iter2.json", fd, "application/json")}
    data = {"name": f"TEST_iter2_{uuid.uuid4().hex[:6]}"}
    r = session.post(f"{API}/requirements/upload", files=files, data=data, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    yield body
    session.delete(f"{API}/requirements/sets/{body['set_id']}", timeout=20)


# ---------- Prompts CRUD: tailoring ----------
def test_create_list_delete_tailoring_prompt(session):
    payload = {
        "name": "TEST_aviation_tailoring",
        "kind": "tailoring",
        "system_prompt": "This project follows DO-178C for safety-critical avionics. Emphasize verifiability and traceability.",
        "description": "TEST tailoring prompt",
    }
    r = session.post(f"{API}/prompts", json=payload, timeout=20)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["kind"] == "tailoring"
    assert p["name"] == payload["name"]
    assert p["system_prompt"] == payload["system_prompt"]
    pid = p["id"]
    assert isinstance(pid, str) and len(pid) > 0

    # list filtered by kind
    r = session.get(f"{API}/prompts?kind=tailoring", timeout=20)
    assert r.status_code == 200
    items = r.json()
    assert any(it["id"] == pid for it in items)
    for it in items:
        assert "_id" not in it
        assert it["kind"] == "tailoring"

    # list all
    r = session.get(f"{API}/prompts", timeout=20)
    assert r.status_code == 200
    assert any(it["id"] == pid for it in r.json())

    # get one
    r = session.get(f"{API}/prompts/{pid}", timeout=20)
    assert r.status_code == 200
    assert r.json()["id"] == pid

    # delete
    r = session.delete(f"{API}/prompts/{pid}", timeout=20)
    assert r.status_code == 200
    assert r.json().get("deleted") is True

    # confirm gone
    r = session.get(f"{API}/prompts/{pid}", timeout=20)
    assert r.status_code == 404


# ---------- Prompts CRUD: classifier with categories ----------
def test_create_classifier_prompt_persists_categories(session):
    payload = {
        "name": "TEST_dept_classifier",
        "kind": "classifier",
        "system_prompt": "Classify requirements by responsible department in an e-commerce platform.",
        "categories": ["Frontend", "Backend", "Security", "Infrastructure"],
        "description": "TEST classifier",
    }
    r = session.post(f"{API}/prompts", json=payload, timeout=20)
    assert r.status_code == 200, r.text
    p = r.json()
    pid = p["id"]
    assert p["kind"] == "classifier"
    assert p["categories"] == payload["categories"]

    # GET to verify persistence
    r = session.get(f"{API}/prompts/{pid}", timeout=20)
    assert r.status_code == 200
    fetched = r.json()
    assert fetched["categories"] == payload["categories"]
    assert fetched["system_prompt"] == payload["system_prompt"]

    # cleanup
    session.delete(f"{API}/prompts/{pid}", timeout=20)


def test_create_prompt_invalid_kind(session):
    payload = {"name": "TEST_bad", "kind": "other", "system_prompt": "x"}
    r = session.post(f"{API}/prompts", json=payload, timeout=20)
    assert r.status_code == 400


# ---------- Generate prompt (LLM) ----------
def test_generate_tailoring_prompt(session):
    payload = {
        "kind": "tailoring",
        "project_description": "We are building a medical device that monitors heart rate. Must comply with IEC 62304.",
        "provider": PROVIDER,
        "model": MODEL,
    }
    r = session.post(f"{API}/prompts/generate", json=payload, timeout=LLM_TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["kind"] == "tailoring"
    assert isinstance(data.get("name"), str) and len(data["name"]) > 0
    assert isinstance(data.get("system_prompt"), str) and len(data["system_prompt"]) > 10
    # tailoring shouldn't include categories
    assert data.get("categories") == []


def test_generate_classifier_prompt(session):
    payload = {
        "kind": "classifier",
        "project_description": "An e-commerce checkout platform. We want to route requirements to the right team.",
        "provider": PROVIDER,
        "model": MODEL,
        "categories_hint": ["Frontend", "Payments", "Infra"],
    }
    r = session.post(f"{API}/prompts/generate", json=payload, timeout=LLM_TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["kind"] == "classifier"
    assert isinstance(data.get("name"), str) and len(data["name"]) > 0
    assert isinstance(data.get("system_prompt"), str) and len(data["system_prompt"]) > 10
    cats = data.get("categories")
    assert isinstance(cats, list) and len(cats) > 0
    for c in cats:
        assert isinstance(c, str) and len(c) > 0


def test_generate_prompt_empty_description(session):
    payload = {"kind": "tailoring", "project_description": "  ", "provider": PROVIDER, "model": MODEL}
    r = session.post(f"{API}/prompts/generate", json=payload, timeout=20)
    assert r.status_code == 400


# ---------- Classify set ----------
@pytest.fixture(scope="module")
def saved_classifier_prompt(session):
    payload = {
        "name": "TEST_secfunc_classifier",
        "kind": "classifier",
        "system_prompt": "Classify each requirement into one category based on its primary concern.",
        "categories": ["Security", "Performance", "Functional", "Logging"],
        "description": "TEST",
    }
    r = session.post(f"{API}/prompts", json=payload, timeout=20)
    assert r.status_code == 200, r.text
    p = r.json()
    yield p
    session.delete(f"{API}/prompts/{p['id']}", timeout=20)


@pytest.fixture(scope="module")
def saved_tailoring_prompt(session):
    payload = {
        "name": "TEST_webapp_tailoring",
        "kind": "tailoring",
        "system_prompt": "This is a secure web application. Emphasize security verifiability and OWASP alignment when evaluating.",
        "description": "TEST",
    }
    r = session.post(f"{API}/prompts", json=payload, timeout=20)
    assert r.status_code == 200, r.text
    p = r.json()
    yield p
    session.delete(f"{API}/prompts/{p['id']}", timeout=20)


def test_classify_set_with_saved_prompt(session, uploaded_set, saved_classifier_prompt):
    payload = {
        "set_id": uploaded_set["set_id"],
        "provider": PROVIDER,
        "model": MODEL,
        "prompt_id": saved_classifier_prompt["id"],
    }
    r = session.post(f"{API}/classify/set", json=payload, timeout=SET_TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "results" in data and isinstance(data["results"], list)
    assert len(data["results"]) == len(uploaded_set["requirements"])
    assert "distribution" in data and isinstance(data["distribution"], dict)
    assert "categories" in data and isinstance(data["categories"], list)
    assert set(data["categories"]) == set(saved_classifier_prompt["categories"])

    # Each result has required fields
    for item in data["results"]:
        assert "requirement_id" in item
        assert isinstance(item.get("primary_category"), str) and len(item["primary_category"]) > 0
        # confidence may come back as int or float from LLM; coerce check
        conf = item.get("confidence")
        assert isinstance(conf, (int, float))
        assert "rationale" in item

    # distribution counts add up to len(results)
    total = sum(data["distribution"].values())
    assert total == len(data["results"]), f"distribution total {total} != results {len(data['results'])}"


def test_classify_set_requires_prompt(session, uploaded_set):
    payload = {"set_id": uploaded_set["set_id"], "provider": PROVIDER, "model": MODEL}
    r = session.post(f"{API}/classify/set", json=payload, timeout=30)
    assert r.status_code == 400
    assert "classifier" in r.json().get("detail", "").lower() or "required" in r.json().get("detail", "").lower()


def test_classify_set_rejects_tailoring_prompt(session, uploaded_set, saved_tailoring_prompt):
    payload = {
        "set_id": uploaded_set["set_id"],
        "provider": PROVIDER,
        "model": MODEL,
        "prompt_id": saved_tailoring_prompt["id"],
    }
    r = session.post(f"{API}/classify/set", json=payload, timeout=30)
    assert r.status_code == 400
    assert "classifier" in r.json().get("detail", "").lower()


# ---------- Tailored analyze ----------
def test_analyze_individual_with_tailoring(session, saved_tailoring_prompt):
    payload = {
        "text": "The system shall be secure.",
        "requirement_id": "REQ-TAIL-1",
        "provider": PROVIDER,
        "model": MODEL,
        "tailoring_prompt_id": saved_tailoring_prompt["id"],
    }
    r = session.post(f"{API}/analyze/individual", json=payload, timeout=LLM_TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "error" not in data, f"tailored analyze errored: {data.get('error')}"
    assert isinstance(data.get("overall_score"), int)
    rules = data.get("rules", {})
    expected = {"necessary", "singular", "unambiguous", "complete",
                "verifiable", "feasible", "consistent", "traceable"}
    assert expected.issubset(set(rules.keys()))


def test_analyze_set_with_tailoring(session, uploaded_set, saved_tailoring_prompt):
    payload = {
        "set_id": uploaded_set["set_id"],
        "provider": PROVIDER,
        "model": MODEL,
        "tailoring_prompt_id": saved_tailoring_prompt["id"],
    }
    r = session.post(f"{API}/analyze/set", json=payload, timeout=SET_TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data["results"], list) and len(data["results"]) == len(uploaded_set["requirements"])
    for item in data["results"]:
        assert "overall_score" in item
        assert "rules" in item
    assert "inconsistencies" in data
