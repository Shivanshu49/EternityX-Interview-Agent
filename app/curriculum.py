"""Load and expose the cohort curriculum once per application process."""

import json
from pathlib import Path
from typing import Any


CURRICULUM_PATH = Path(__file__).resolve().parent.parent / "curriculum.json"


def _load_curriculum() -> dict[str, Any]:
    try:
        with CURRICULUM_PATH.open(encoding="utf-8") as curriculum_file:
            curriculum = json.load(curriculum_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Unable to load curriculum from {CURRICULUM_PATH}."
        ) from exc

    if not isinstance(curriculum, dict) or not isinstance(curriculum.get("days"), list):
        raise RuntimeError("Curriculum must be an object containing a days array.")
    return curriculum


CURRICULUM = _load_curriculum()
