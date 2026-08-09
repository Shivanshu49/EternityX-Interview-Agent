"""LLM-judged answer evaluation. Owner: A.

The other half of the adaptive loop. `question_engine.needs_follow_up` has
always *preferred* a grade when one exists on the turn; until now nothing ever
produced one, so every depth decision fell back to `is_shallow()`, which counts
words and pattern-matches identifiers. That heuristic cannot tell a long,
confident, wrong answer from a right one, nor a short precise answer from a
shrug.

`evaluate_answer` grades one answer against the day's actual objectives, in one
structured call. It is deliberately fail-open: any model or transport problem
returns None and the caller falls back to the heuristic, because a grading
outage must never take the interview down with it.

Everything here is gated behind ENABLE_ADAPTIVE_EVAL, off by default. The
heuristic path stays exactly what main runs until this has earned its way in.
"""

from __future__ import annotations

import os

from app import llm, prompts
from app.models import (
    AnswerEvaluation,
    CurriculumDay,
    DayTurn,
    Mission,
    UnderstandingLevel,
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


# Read once at import, same as every other config in this app. Tests flip the
# module attribute; a deployment sets the environment variable and restarts.
ENABLE_ADAPTIVE_EVAL = _env_flag("ENABLE_ADAPTIVE_EVAL")

# Grading is judgment work, so effort stays at medium (see the discussion on
# DEFAULT_EFFORT in llm.py). The output is one small object; the budget is
# headroom for thinking, not for prose.
GRADER_EFFORT = "medium"
GRADER_MAX_TOKENS = 2500


GRADER_SYSTEM_PROMPT = """\
You are grading one answer from a live technical interview. You are given the
curriculum day the question came from, including what the candidate was
supposed to be able to do, the candidate's record on that day's mission, the
question that was asked, and the answer they gave.

Grade the answer against the day's objectives, on its content alone. Length is
not understanding: a long, confident, wrong answer scores low, and a short
answer that names the exact mechanism scores high. Keywords only count when the
answer uses them correctly; name-dropping a tool is recall, not application.

If the answer does not address the question that was asked, grade what it
demonstrates about this day's objectives, which may be nothing.

Be strict about the difference between reciting and using. "Embeddings capture
semantic meaning" is recall. "The query and document embedders have to match or
the cosine scores are meaningless" is applied understanding.

You are not writing to the candidate and you are not writing feedback. You are
producing a grade another system will act on."""


def build_grading_payload(
    turn: DayTurn,
    curriculum_day: CurriculumDay | None = None,
    mission: Mission | None = None,
) -> dict[str, object]:
    """One user message: the day, the record, the exchange, the ask."""
    body = [
        prompts.render_day_brief(curriculum_day, mission),
        "",
        f"Question asked:\n{turn.question}",
        "",
        f"Candidate's answer:\n{turn.answer}",
        "",
        "Grade this answer.",
    ]
    return {
        "system": GRADER_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": "\n".join(body)}],
    }


def _non_answer_grade() -> AnswerEvaluation:
    """Blank and 'I don't know' need no model call to grade."""
    return AnswerEvaluation(
        score=0.0,
        level=UnderstandingLevel.NONE,
        reasoning=(
            "The candidate gave no substantive answer -- blank, or an explicit "
            "'don't know'."
        ),
        gaps=["Did not engage with the question."],
        needs_follow_up=True,
    )


def evaluate_answer(
    turn: DayTurn,
    *,
    curriculum_day: CurriculumDay | None = None,
    mission: Mission | None = None,
    client: llm.SupportsMessages | None = None,
    model: str = llm.DEFAULT_MODEL,
    max_tokens: int = GRADER_MAX_TOKENS,
    effort: str = GRADER_EFFORT,
) -> AnswerEvaluation | None:
    """Grade one answered turn. Returns None when no grade could be produced.

    None is a deliberate contract: the caller treats it as "no grader ran" and
    the heuristic path takes over, so a model outage degrades the interview's
    judgment rather than its availability.
    """
    # Import here rather than at module top: question_engine imports this
    # module, and the non-answer phrase list must not be duplicated to break
    # the cycle.
    from app.question_engine import is_non_answer

    if is_non_answer(turn.answer):
        return _non_answer_grade()

    try:
        raw = llm.complete_json(
            build_grading_payload(turn, curriculum_day, mission),
            AnswerEvaluation.model_json_schema(),
            client=client,
            model=model,
            max_tokens=max_tokens,
            effort=effort,
        )
        return AnswerEvaluation.model_validate(raw)
    except Exception:  # noqa: BLE001 -- deliberately fail-open
        # Broad on purpose. LLMError covers the model saying something
        # unusable, but a transport failure surfaces as an SDK or httpx
        # exception, and the contract of this function is that *no* grading
        # failure of any kind is allowed to become an interview failure. The
        # question call that follows uses the same provider, so a systemic
        # outage still surfaces -- once, with the right blame.
        return None
