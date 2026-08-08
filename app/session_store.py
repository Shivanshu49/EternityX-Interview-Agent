"""Process-local interview session storage.

The hackathon specification explicitly does not require persistence. Sessions
therefore disappear whenever the API process restarts.
"""

from typing import Any


sessions: dict[str, dict[str, Any]] = {}


def create_session(session_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    session = {
        "candidate": candidate,
        "history": [],
        "days_covered": [],
        "questions_asked": 0,
        "phase": "questioning",
    }
    sessions[session_id] = session
    return session


def get_session(session_id: str) -> dict[str, Any] | None:
    return sessions.get(session_id)


def update_session(session_id: str, **kwargs: Any) -> dict[str, Any]:
    sessions[session_id].update(kwargs)
    return sessions[session_id]


def delete_session(session_id: str) -> None:
    sessions.pop(session_id, None)


def can_finish(session: dict[str, Any]) -> bool:
    """Enforce the challenge's non-negotiable minimum interview length."""

    return (
        session["questions_asked"] >= 8
        and len(set(session["days_covered"])) >= 4
    )
