"""Structured post-interview report.

Thin seam only: `routes.py` calls `generate(session) -> dict`, and this keeps
that signature stable while the real work lives in `app/feedback_engine.py`.
"""

from typing import Any

from app.curriculum import CURRICULUM
from app.feedback_engine import generate_feedback
from app.models import turns_from_history


def generate(session: dict[str, Any]) -> dict[str, Any]:
    """Produce the spec's summary/strengths/gaps/next block for a finished session."""
    return generate_feedback(session, CURRICULUM).model_dump()


def fallback_generate(session: dict[str, Any]) -> dict[str, Any]:
    """Produce conservative, actionable feedback when the model is unavailable."""
    turns = [turn for turn in turns_from_history(session.get("history", [])) if turn.answer]
    covered = list(dict.fromkeys(turn.day for turn in turns))
    titles = {
        day: (CURRICULUM.get(day).title if CURRICULUM.get(day) else f"Day {day}")
        for day in covered
    }

    strongest = max(turns, key=lambda turn: len(turn.answer), default=None)
    thinnest = min(turns, key=lambda turn: len(turn.answer), default=None)
    if strongest:
        excerpt = " ".join(strongest.answer.split())[:160].rstrip()
        strength = (
            f"On Day {strongest.day} ({titles[strongest.day]}), you gave your "
            f"most developed explanation: {excerpt}"
        )
    else:
        strength = "You stayed engaged through the complete interview flow."

    if thinnest:
        gap = (
            f"Day {thinnest.day} ({titles[thinnest.day]}) needs a more concrete "
            "answer with an explicit design choice, failure mode, and validation step."
        )
    else:
        gap = "The available answers were too limited to assess technical depth reliably."

    next_steps = [
        f"Revisit Day {day} ({titles[day]}) and build a small example that records "
        "one design choice, one failure mode, and one measurable success criterion."
        for day in covered[:2]
    ] or ["Practice one cohort topic with a concrete architecture and failure analysis."]

    topic_list = ", ".join(f"Day {day}" for day in covered) or "the cohort topics"
    return {
        "summary": (
            f"You completed the required interview breadth across {topic_list}. "
            "This assessment is intentionally conservative and focuses on the "
            "specific evidence present in your answers."
        ),
        "strengths": [strength],
        "gaps": [gap],
        "next": next_steps,
    }
