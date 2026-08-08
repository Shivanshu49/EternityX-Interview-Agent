"""End-to-end API contract and hard-gate tests."""

from collections.abc import Callable

import pytest

from app import question_engine, report
from app.curriculum import CURRICULUM
from app.session_store import get_session


def install_question_schedule(
    monkeypatch: pytest.MonkeyPatch, days: list[int]
) -> Callable[[dict], dict]:
    def fake_next_question(
        session: dict, curriculum: dict, client=None
    ) -> dict:
        assert curriculum is CURRICULUM
        index = session["questions_asked"]
        return {"reply": f"Question {index + 1}", "day": days[index]}

    monkeypatch.setattr(question_engine, "next_question", fake_next_question)
    return fake_next_question


def start(client, candidate, session_id="session-1"):
    return client.post(
        "/api/interview",
        json={"sessionId": session_id, "candidate": candidate},
    )


def turn(client, number: int, session_id="session-1"):
    return client.post(
        "/api/interview",
        json={"sessionId": session_id, "message": f"Answer {number}"},
    )


def test_start_creates_session_and_returns_exact_response(
    client, candidate, monkeypatch
):
    install_question_schedule(monkeypatch, [7])

    response = start(client, candidate)

    assert response.status_code == 200
    assert response.json() == {"reply": "Question 1", "done": False}
    session = get_session("session-1")
    assert session["candidate"] == candidate
    assert session["questions_asked"] == 1
    assert session["days_covered"] == [7]
    assert session["history"] == [
        {"role": "interviewer", "content": "Question 1", "day": 7}
    ]


def test_turn_accumulates_role_ordered_history(client, candidate, monkeypatch):
    install_question_schedule(monkeypatch, [7, 8])
    start(client, candidate)

    response = turn(client, 1)

    assert response.json() == {"reply": "Question 2", "done": False}
    session = get_session("session-1")
    assert session["questions_asked"] == 2
    assert session["days_covered"] == [7, 8]
    assert session["history"] == [
        {"role": "interviewer", "content": "Question 1", "day": 7},
        {"role": "candidate", "content": "Answer 1"},
        {"role": "interviewer", "content": "Question 2", "day": 8},
    ]


def test_rich_question_metadata_is_retained_in_history(
    client, candidate, monkeypatch
):
    def rich_question(session, curriculum, client=None):
        return {
            "reply": "Explain your retrieval choice.",
            "day": 10,
            "tier": "advanced",
            "pattern": "tradeoff",
            "reason": "Candidate needed multiple attempts on retrieval.",
            "is_follow_up": False,
            "learning_signal": "high_attempts",
        }

    monkeypatch.setattr(question_engine, "next_question", rich_question)

    response = start(client, candidate)

    assert response.status_code == 200
    assert get_session("session-1")["history"][0] == {
        "role": "interviewer",
        "content": "Explain your retrieval choice.",
        "day": 10,
        "tier": "advanced",
        "pattern": "tradeoff",
        "reason": "Candidate needed multiple attempts on retrieval.",
        "is_follow_up": False,
        "learning_signal": "high_attempts",
    }


def test_seven_questions_cannot_finish_even_with_four_days(
    client, candidate, monkeypatch
):
    install_question_schedule(monkeypatch, [7, 8, 10, 12, 7, 8, 10, 12])
    start(client, candidate)
    for answer_number in range(1, 7):
        response = turn(client, answer_number)
        assert response.json()["done"] is False

    assert get_session("session-1")["questions_asked"] == 7
    response = turn(client, 7)

    assert response.json() == {"reply": "Question 8", "done": False}
    assert get_session("session-1")["questions_asked"] == 8


def test_eight_questions_cannot_finish_with_only_three_days(
    client, candidate, monkeypatch
):
    install_question_schedule(monkeypatch, [7, 8, 10, 7, 8, 10, 7, 8, 12])
    start(client, candidate)
    for answer_number in range(1, 8):
        assert turn(client, answer_number).json()["done"] is False

    response = turn(client, 8)

    assert response.json() == {"reply": "Question 9", "done": False}
    session = get_session("session-1")
    assert session["questions_asked"] == 9
    assert len(set(session["days_covered"])) == 4


def test_full_interview_finishes_after_eight_answered_questions(
    client, candidate, monkeypatch
):
    install_question_schedule(monkeypatch, [7, 7, 8, 8, 10, 10, 12, 12])
    expected_feedback = {
        "summary": "Strong interview.",
        "strengths": ["Explained retrieval clearly."],
        "gaps": ["Needs more detail on evaluation."],
        "next": ["Practice evaluation design."],
    }
    monkeypatch.setattr(report, "generate", lambda session: expected_feedback)
    start(client, candidate)
    for answer_number in range(1, 8):
        response = turn(client, answer_number)
        assert response.json()["done"] is False

    response = turn(client, 8)

    assert response.status_code == 200
    assert response.json() == {
        "reply": "Interview completed.",
        "done": True,
        "feedback": expected_feedback,
    }
    session = get_session("session-1")
    assert session["phase"] == "done"
    assert session["questions_asked"] == 8
    assert len(session["history"]) == 16


def test_bundled_stubs_can_run_a_complete_interview(client, candidate):
    response = start(client, candidate, session_id="stub-session")
    assert response.status_code == 200

    for answer_number in range(1, 9):
        response = turn(client, answer_number, session_id="stub-session")

    body = response.json()
    assert body["done"] is True
    assert set(body["feedback"]) == {"summary", "strengths", "gaps", "next"}
    assert get_session("stub-session")["questions_asked"] == 8
    # `can_finish` gates on >= 4 distinct days. The original `== 4` matched the
    # placeholder engine, which round-robined a fixed four; the adaptive engine
    # spreads wider, and covering more days is not a regression.
    assert len(set(get_session("stub-session")["days_covered"])) >= 4


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        ({"candidate": {"member": {}}}, 422),
        ({"sessionId": " ", "candidate": {"member": {}}}, 422),
        ({"sessionId": "s"}, 400),
        ({"sessionId": "s", "candidate": {}}, 400),
        ({"sessionId": "s", "message": " "}, 422),
        (
            {
                "sessionId": "s",
                "candidate": {"member": {}},
                "message": "answer",
            },
            400,
        ),
        ({"sessionId": "s", "candidate": {"member": {}}, "extra": True}, 422),
    ],
)
def test_invalid_request_shapes_return_controlled_errors(
    client, payload, expected_status
):
    response = client.post("/api/interview", json=payload)
    assert response.status_code == expected_status
    assert "detail" in response.json()


def test_malformed_json_returns_422(client):
    response = client.post(
        "/api/interview",
        content="{",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422


def test_unknown_duplicate_and_completed_sessions(
    client, candidate, monkeypatch
):
    unknown = turn(client, 1, session_id="missing")
    assert unknown.status_code == 404

    install_question_schedule(monkeypatch, [7, 7, 8, 8, 10, 10, 12, 12])
    assert start(client, candidate).status_code == 200
    assert start(client, candidate).status_code == 409
    for answer_number in range(1, 9):
        completed = turn(client, answer_number)
    assert completed.json()["done"] is True
    assert turn(client, 9).status_code == 409


def test_invalid_question_result_is_502_and_start_is_rolled_back(
    client, candidate, monkeypatch
):
    monkeypatch.setattr(
        question_engine,
        "next_question",
        lambda session, curriculum, client=None: {"reply": "Missing day"},
    )

    response = start(client, candidate)

    assert response.status_code == 502
    assert get_session("session-1") is None


def test_question_failure_does_not_commit_candidate_answer(
    client, candidate, monkeypatch
):
    install_question_schedule(monkeypatch, [7])
    start(client, candidate)
    original_history = list(get_session("session-1")["history"])

    def fail(_session, _curriculum, client=None):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(question_engine, "next_question", fail)
    response = turn(client, 1)

    assert response.status_code == 503
    assert get_session("session-1")["history"] == original_history
    assert get_session("session-1")["questions_asked"] == 1


def test_feedback_failure_does_not_commit_final_answer(
    client, candidate, monkeypatch
):
    install_question_schedule(monkeypatch, [7, 7, 8, 8, 10, 10, 12, 12])
    start(client, candidate)
    for answer_number in range(1, 8):
        turn(client, answer_number)
    history_before_final_answer = list(get_session("session-1")["history"])

    def fail(_session):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(report, "generate", fail)
    response = turn(client, 8)

    assert response.status_code == 503
    session = get_session("session-1")
    assert session["phase"] == "questioning"
    assert session["history"] == history_before_final_answer
