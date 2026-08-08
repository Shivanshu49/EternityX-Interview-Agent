"""Unit tests for the process-local session store."""

from app.session_store import can_finish, create_session, get_session, update_session


def test_create_get_and_update_session(candidate):
    created = create_session("session-1", candidate)

    assert get_session("session-1") is created
    assert created == {
        "candidate": candidate,
        "history": [],
        "days_covered": [],
        "questions_asked": 0,
        "phase": "questioning",
    }

    updated = update_session("session-1", phase="wrapping_up")
    assert updated["phase"] == "wrapping_up"


def test_gate_requires_both_minimums(candidate):
    session = create_session("session-1", candidate)

    session.update(questions_asked=7, days_covered=[7, 8, 10, 12])
    assert can_finish(session) is False

    session.update(questions_asked=8, days_covered=[7, 7, 8, 8, 10, 10, 7, 8])
    assert can_finish(session) is False

    session.update(questions_asked=8, days_covered=[7, 7, 8, 8, 10, 10, 12, 12])
    assert can_finish(session) is True
