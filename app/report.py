"""Structured post-interview report.

Thin seam only: `routes.py` calls `generate(session) -> dict`, and this keeps
that signature stable while the real work lives in `app/feedback_engine.py`.
"""

from typing import Any

from app.curriculum import CURRICULUM
from app.feedback_engine import generate_feedback


def generate(session: dict[str, Any]) -> dict[str, Any]:
    """Produce the spec's summary/strengths/gaps/next block for a finished session."""
    return generate_feedback(session, CURRICULUM).model_dump()
