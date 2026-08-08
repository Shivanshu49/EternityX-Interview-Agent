"""Shared test fixtures."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app
from app.session_store import sessions


def _stub_json_for(schema: dict) -> str:
    """Build a minimal object satisfying `schema`, so structured calls parse.

    Structured-output requests are guaranteed valid JSON by the API, so a stub
    that returned prose would be testing a state that cannot happen.
    """
    out = {}
    for name, spec in schema.get("properties", {}).items():
        if spec.get("type") == "array":
            out[name] = [f"Stub {name} item."]
        elif spec.get("type") == "string":
            out[name] = f"Stub {name}."
        elif spec.get("type") in ("integer", "number"):
            out[name] = 1
        elif spec.get("type") == "boolean":
            out[name] = False
    return json.dumps(out)


class _StubMessages:
    """Stands in for `client.messages`, for both plain and structured calls."""

    def create(self, **kwargs):
        schema = (
            kwargs.get("output_config", {}).get("format", {}).get("schema")
        )
        if schema:
            text = _stub_json_for(schema)
        else:
            brief = kwargs["messages"][-1]["content"]
            day = brief.partition("Day ")[2].partition(":")[0] or "?"
            text = f"Stub question about day {day}."
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason="end_turn",
        )


class StubAnthropic:
    def __init__(self) -> None:
        self.messages = _StubMessages()
        self.beta = SimpleNamespace(messages=_StubMessages())


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    """Guarantee no test reaches the Anthropic API.

    The question engine is genuinely LLM-backed, so any test that exercises it
    without a mock would need credentials and would make a billed network call.
    Autouse so that stays true for tests written later.
    """
    monkeypatch.setattr(llm, "get_client", StubAnthropic)


@pytest.fixture(autouse=True)
def empty_session_store():
    sessions.clear()
    yield
    sessions.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def candidate() -> dict:
    return {
        "member": {
            "id": "CAND-TEST",
            "name": "Test Candidate",
            "jobRole": "AI Engineer",
            "yearsExperience": 2,
            "education": "BS Computer Science",
            "status": "COMPLETED",
        },
        "missions": [
            {"day": 7, "title": "Embeddings", "passed": True, "attempts": 1},
            {"day": 8, "title": "Vector Databases", "passed": True, "attempts": 2},
            {"day": 10, "title": "Retrieval", "passed": True, "attempts": 1},
            {"day": 12, "title": "Prompting", "passed": True, "attempts": 3},
        ],
        "signals": {
            "commitDays": 25,
            "missionsCompleted": 28,
            "missionsFirstTry": 15,
        },
    }
