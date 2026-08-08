"""Temporary structured post-interview report generator."""

from typing import Any


def generate(session: dict[str, Any]) -> dict[str, Any]:
    """Return spec-compliant placeholder feedback until the real engine lands."""

    return {
        "summary": (
            f"Completed {session['questions_asked']} questions across "
            f"{len(set(session['days_covered']))} curriculum days."
        ),
        "strengths": ["Completed the full technical interview."],
        "gaps": ["Detailed competency scoring is not connected yet."],
        "next": ["Review the interview transcript and revisit weaker topics."],
    }
