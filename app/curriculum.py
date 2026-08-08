"""Load and expose the cohort curriculum once per application process.

Parsed into the typed `Curriculum` model at import time rather than left as a
raw dict, so schema errors surface at startup instead of on the first request,
and no request pays to re-validate 31 days.
"""

from pathlib import Path

from pydantic import ValidationError

from app.models import Curriculum


CURRICULUM_PATH = Path(__file__).resolve().parent.parent / "curriculum.json"


def _load_curriculum() -> Curriculum:
    try:
        curriculum = Curriculum.load(CURRICULUM_PATH)
    except (OSError, ValueError, ValidationError) as exc:
        raise RuntimeError(
            f"Unable to load curriculum from {CURRICULUM_PATH}."
        ) from exc

    if not curriculum.days:
        raise RuntimeError("Curriculum must contain a non-empty days array.")
    return curriculum


CURRICULUM = _load_curriculum()
