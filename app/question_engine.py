"""Temporary deterministic question engine.

Owner A can replace the body with an adaptive/LLM implementation while keeping
the ``next_question(session) -> {reply, day}`` contract unchanged.
"""

from typing import Any


def _candidate_days(candidate: dict[str, Any]) -> list[int]:
    missions = candidate.get("missions", [])
    passed = [
        mission.get("day")
        for mission in missions
        if mission.get("passed") is True and isinstance(mission.get("day"), int)
    ]
    other = [
        mission.get("day")
        for mission in missions
        if isinstance(mission.get("day"), int) and mission.get("day") not in passed
    ]

    days: list[int] = []
    for day in [*passed, *other, 1, 2, 3, 4]:
        if 1 <= day <= 31 and day not in days:
            days.append(day)
    return days


def next_question(session: dict[str, Any]) -> dict[str, Any]:
    """Return a runnable placeholder question spanning at least four days."""

    days = _candidate_days(session["candidate"])
    index = session["questions_asked"]
    day = days[index % max(4, min(len(days), 4))]
    member = session["candidate"].get("member", {})
    name = member.get("name", "candidate")
    return {
        "reply": f"{name}, question {index + 1}: explain a key decision from day {day}.",
        "day": day,
    }
