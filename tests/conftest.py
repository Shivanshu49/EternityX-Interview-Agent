"""Shared test fixtures."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.session_store import sessions


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
