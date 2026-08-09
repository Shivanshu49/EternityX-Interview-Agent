"""The adaptive loop: LLM-judged grading, belief updates, and what changes.

The centrepiece is the comparison section: the same two answers pushed through
the old heuristic path and the graded path, showing the two failure modes the
word-count heuristic cannot avoid -- waving through a long confident wrong
answer, and probing a short precise right one.

Everything here runs with explicit stub clients. ENABLE_ADAPTIVE_EVAL stays
False process-wide; tests that need the flag flip the module attribute.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import evaluation, prompts
from app import question_engine as qe
from app.curriculum import CURRICULUM
from app.models import (
    AnswerEvaluation,
    Candidate,
    CohortSignals,
    DayTurn,
    InterviewSession,
    Member,
    Mission,
    QuestionMode,
    UnderstandingLevel,
    turns_from_history,
)
from app.signals import build_profiles, fold_beliefs, unknown_profile, update_belief


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

DAY = 11  # RAG End-to-End: real objectives in curriculum.json

# 48 words, tool names, confident, and wrong from the first clause. The
# heuristic scores length and specificity; a grader scores content.
LONG_CONFIDENT_WRONG = (
    "For retrieval you always want the biggest chunks possible because more "
    "context means the embedding captures more meaning, and cosine similarity "
    "works best on exact keyword matches, so I would raise chunk size to the "
    "maximum and duplicate the query keywords into every chunk before indexing "
    "with HNSW."
)

# 11 words, no tool names, and names the exact mechanism.
SHORT_PRECISE_RIGHT = (
    "Same embedding model on both sides, or cosine scores are meaningless."
)

WRONG_GRADE = {
    "score": 0.15,
    "level": "recall",
    "reasoning": "Confidently wrong: oversized chunks dilute the embedding and "
    "cosine similarity is not keyword matching.",
    "strengths": [],
    "gaps": ["Believes bigger chunks always help.", "Conflates semantic and lexical match."],
    "needs_follow_up": False,
}

RIGHT_GRADE = {
    "score": 0.85,
    "level": "applied",
    "reasoning": "Names the query/document embedder mismatch and its exact "
    "consequence for cosine scores.",
    "strengths": ["Knows both sides must share an embedding model."],
    "gaps": [],
    "needs_follow_up": False,
}


def grinder_candidate(day: int = DAY, **mission_kw) -> Candidate:
    """Passed after many attempts: the prior the record says least about."""
    mission = Mission(day=day, title="RAG", passed=True, attempts=4, **mission_kw)
    return Candidate(
        member=Member(id="c-1", name="Test Person"),
        missions=[mission],
        signals=CohortSignals(commitDays=20, missionsCompleted=10, missionsFirstTry=2),
    )


def one_turn_session(answer: str, candidate: Candidate | None = None) -> InterviewSession:
    return InterviewSession(
        candidate=candidate or grinder_candidate(),
        turns=[DayTurn(day=DAY, question="Walk me through your RAG retrieval.", answer=answer)],
    )


class GraderStub:
    """Answers grading calls with `grade` and question calls with plain text.

    Grading calls are recognisable by the json_schema in output_config, which is
    also how the calls are counted apart.
    """

    def __init__(self, grade: dict | Exception | None = None) -> None:
        self.grade = grade
        self.calls: list[dict] = []
        self.messages = self
        self.beta = SimpleNamespace(messages=self)

    def grading_calls(self) -> list[dict]:
        return [
            c for c in self.calls
            if c.get("output_config", {}).get("format", {}).get("type") == "json_schema"
        ]

    def create(self, **kwargs):
        self.calls.append(kwargs)
        is_grading = bool(kwargs.get("output_config", {}).get("format"))
        if is_grading and isinstance(self.grade, Exception):
            raise self.grade
        text = json.dumps(self.grade) if is_grading else "Stub follow-up question?"
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason="end_turn",
        )


# ==========================================================================
# The comparison: same answers, heuristic vs graded
# ==========================================================================


def test_heuristic_waves_through_a_long_confident_wrong_answer():
    """48 words and an HNSW name-drop clear the old bar. That is the defect."""
    session = one_turn_session(LONG_CONFIDENT_WRONG)

    assert qe.is_shallow(
        LONG_CONFIDENT_WRONG,
        curriculum_day=CURRICULUM.get(DAY),
        vocabulary=CURRICULUM.tool_vocabulary(),
    ) is False

    target = qe.resolve_target(session, CURRICULUM)
    assert target.mode is QuestionMode.OPENING, "heuristic moves on"
    assert target.day != DAY


def test_grading_probes_the_same_wrong_answer():
    session = one_turn_session(LONG_CONFIDENT_WRONG)
    grade = AnswerEvaluation.model_validate(WRONG_GRADE)

    target = qe.resolve_target(session, CURRICULUM, last_evaluation=grade)

    assert target.mode is QuestionMode.FOLLOW_UP, "graded path stays and digs"
    assert target.day == DAY
    assert "0.15" in target.reason and "recall" in target.reason


def test_heuristic_probes_a_short_precise_right_answer():
    """11 words fails the word count no matter how exact they are."""
    session = one_turn_session(SHORT_PRECISE_RIGHT)

    target = qe.resolve_target(session, CURRICULUM)
    assert target.mode is QuestionMode.FOLLOW_UP, "heuristic wastes a probe"
    assert target.day == DAY


def test_grading_moves_on_from_the_same_right_answer():
    session = one_turn_session(SHORT_PRECISE_RIGHT)
    grade = AnswerEvaluation.model_validate(RIGHT_GRADE)

    target = qe.resolve_target(session, CURRICULUM, last_evaluation=grade)

    assert target.mode is QuestionMode.OPENING, "0.85 applied clears the day"
    assert target.day != DAY


# ==========================================================================
# Belief-driven probing
# ==========================================================================


def middling_grade(score: float = 0.55) -> AnswerEvaluation:
    return AnswerEvaluation(
        score=score,
        level=UnderstandingLevel.APPLIED,
        reasoning="Partially right.",
        needs_follow_up=False,
    )


def test_one_passing_answer_does_not_clear_a_grinder_day():
    """Score 0.55 passes every per-answer check, yet the probe still fires.

    GRINDER prior (0.40, 0.75) + one 0.55 gives mastery 0.49, uncertainty 0.30:
    still a coin flip, still unresolved. This behaviour is unreachable from the
    per-answer check alone; it exists only because update_belief feeds back.
    """
    session = one_turn_session(LONG_CONFIDENT_WRONG)

    target = qe.resolve_target(session, CURRICULUM, last_evaluation=middling_grade())

    assert target.mode is QuestionMode.FOLLOW_UP
    assert target.answer_was_thin is False, "the answer held up; the day did not"
    assert "unresolved" in target.reason
    assert "0.49" in target.reason and "0.30" in target.reason


def test_a_failed_mission_record_counts_as_evidence_and_blocks_the_probe():
    """STRUGGLED prior (0.20, 0.45): the record already observed them failing.

    Same 0.55 answer, but uncertainty lands at 0.18 (< 0.25), so the belief is
    considered settled enough and the interview moves on.
    """
    candidate = grinder_candidate()
    candidate.missions[0] = Mission(day=DAY, title="RAG", passed=False, attempts=4)
    session = one_turn_session(LONG_CONFIDENT_WRONG, candidate)

    target = qe.resolve_target(session, CURRICULUM, last_evaluation=middling_grade())

    assert target.mode is QuestionMode.OPENING


def test_belief_probe_respects_the_follow_up_budget():
    """A second belief probe on the same day must not fire past the budget."""
    session = InterviewSession(
        candidate=grinder_candidate(),
        turns=[
            DayTurn(day=DAY, question="Q1", answer=LONG_CONFIDENT_WRONG),
            DayTurn(
                day=DAY, question="Q2", answer=LONG_CONFIDENT_WRONG, follow_up=True
            ),
        ],
    )

    target = qe.resolve_target(session, CURRICULUM, last_evaluation=middling_grade())

    assert target.mode is QuestionMode.OPENING, "budget spent; move on"


def test_belief_trajectory_converges_and_uncertainty_shrinks():
    profiles = build_profiles(grinder_candidate(), CURRICULUM)
    prior = profiles[DAY]
    assert (prior.mastery, prior.uncertainty) == (0.40, 0.75)

    good = AnswerEvaluation(
        score=0.9, level=UnderstandingLevel.TRANSFERRED, reasoning="Strong."
    )
    turns = [
        DayTurn(day=DAY, question="Q1", answer="a1", evaluation=good),
        DayTurn(day=DAY, question="Q2", answer="a2", evaluation=good, follow_up=True),
    ]

    once = fold_beliefs(profiles, turns[:1])[DAY]
    twice = fold_beliefs(profiles, turns)[DAY]

    assert (once.mastery, once.uncertainty) == (0.70, 0.30)
    assert (twice.mastery, twice.uncertainty) == (0.82, 0.12)
    assert profiles[DAY].mastery == 0.40, "fold must not mutate the priors"


def test_ungraded_turns_are_absence_of_evidence():
    profiles = build_profiles(grinder_candidate(), CURRICULUM)
    turns = [DayTurn(day=DAY, question="Q", answer="whatever")]  # no evaluation
    assert fold_beliefs(profiles, turns)[DAY] == profiles[DAY]


def test_grades_on_days_without_a_mission_row_land_on_the_unknown_prior():
    graded = DayTurn(
        day=3, question="Q", answer="a",
        evaluation=AnswerEvaluation(
            score=1.0, level=UnderstandingLevel.TRANSFERRED, reasoning="Perfect."
        ),
    )
    beliefs = fold_beliefs({}, [graded])

    prior = unknown_profile(3)
    expected = update_belief(prior, 1.0)
    assert beliefs[3] == expected


# ==========================================================================
# The grader itself
# ==========================================================================


def test_non_answers_are_graded_without_a_model_call():
    stub = GraderStub()
    turn = DayTurn(day=DAY, question="Q", answer="  I don't know, honestly.")

    grade = evaluation.evaluate_answer(turn, client=stub)

    assert stub.calls == [], "a shrug needs no tokens to grade"
    assert grade.score == 0.0
    assert grade.level is UnderstandingLevel.NONE
    assert grade.needs_follow_up is True


def test_grading_prompt_carries_the_days_objectives_and_the_exchange():
    day = CURRICULUM.get(DAY)
    turn = DayTurn(day=DAY, question="How did retrieval work?", answer="We used top-k.")

    payload = evaluation.build_grading_payload(
        turn, curriculum_day=day, mission=grinder_candidate().mission_for(DAY)
    )
    body = payload["messages"][0]["content"]

    assert day.title in body
    for objective in day.objectives:
        assert objective in body
    assert "How did retrieval work?" in body
    assert "We used top-k." in body
    assert "passed" in body, "the mission record is part of the grading context"
    assert "grading one answer" in payload["system"]


def test_grader_output_is_validated_into_the_model():
    stub = GraderStub(RIGHT_GRADE)
    turn = DayTurn(day=DAY, question="Q", answer=SHORT_PRECISE_RIGHT)

    grade = evaluation.evaluate_answer(
        turn, curriculum_day=CURRICULUM.get(DAY), client=stub
    )

    assert isinstance(grade, AnswerEvaluation)
    assert grade.score == 0.85
    assert grade.level is UnderstandingLevel.APPLIED
    assert len(stub.grading_calls()) == 1


def test_grader_failure_returns_none_rather_than_raising():
    stub = GraderStub(RuntimeError("gateway fell over"))
    turn = DayTurn(day=DAY, question="Q", answer=SHORT_PRECISE_RIGHT)

    assert evaluation.evaluate_answer(turn, client=stub) is None


def test_grader_junk_json_returns_none():
    stub = GraderStub({"score": "not a number"})
    turn = DayTurn(day=DAY, question="Q", answer=SHORT_PRECISE_RIGHT)

    assert evaluation.evaluate_answer(turn, client=stub) is None


def test_evaluation_schema_forbids_stray_fields():
    assert AnswerEvaluation.model_json_schema()["additionalProperties"] is False


# ==========================================================================
# next_question end to end, flag off and on
# ==========================================================================


def test_flag_off_never_grades(monkeypatch):
    monkeypatch.setattr(evaluation, "ENABLE_ADAPTIVE_EVAL", False)
    stub = GraderStub(WRONG_GRADE)
    session = one_turn_session(LONG_CONFIDENT_WRONG)

    result = qe.next_question(session, CURRICULUM, client=stub)

    assert stub.grading_calls() == [], "flag off must mean zero grading calls"
    assert len(stub.calls) == 1, "exactly the question call, nothing else"
    assert result.last_evaluation is None
    assert result.is_follow_up is False, "heuristic verdict: long answer, move on"


def test_flag_on_grades_once_and_the_grade_drives_the_turn(monkeypatch):
    monkeypatch.setattr(evaluation, "ENABLE_ADAPTIVE_EVAL", True)
    stub = GraderStub(WRONG_GRADE)
    session = one_turn_session(LONG_CONFIDENT_WRONG)

    result = qe.next_question(session, CURRICULUM, client=stub)

    assert len(stub.grading_calls()) == 1
    assert len(stub.calls) == 2, "one grading call plus one question call"
    assert result.is_follow_up is True, "same answer, opposite verdict to flag-off"
    assert result.day == DAY
    assert result.last_evaluation.score == 0.15, "the grade rides back for persistence"


def test_flag_on_grader_failure_falls_back_to_the_heuristic(monkeypatch):
    monkeypatch.setattr(evaluation, "ENABLE_ADAPTIVE_EVAL", True)
    stub = GraderStub(RuntimeError("gateway fell over"))
    session = one_turn_session(LONG_CONFIDENT_WRONG)

    result = qe.next_question(session, CURRICULUM, client=stub)

    assert result is not None, "a grading outage must not stall the interview"
    assert result.is_follow_up is False, "heuristic verdict applies"
    assert result.last_evaluation is None


def test_flag_on_does_not_regrade_an_already_graded_turn(monkeypatch):
    monkeypatch.setattr(evaluation, "ENABLE_ADAPTIVE_EVAL", True)
    stub = GraderStub(WRONG_GRADE)
    session = one_turn_session(LONG_CONFIDENT_WRONG)
    session.turns[-1].evaluation = AnswerEvaluation.model_validate(RIGHT_GRADE)

    result = qe.next_question(session, CURRICULUM, client=stub)

    assert stub.grading_calls() == [], "a stored grade is reused, not repeated"
    assert result.last_evaluation is None, "nothing new to persist"


# ==========================================================================
# Persistence: grades survive the dict round trip
# ==========================================================================


def test_turns_from_history_restores_a_stored_grade():
    history = [
        {"role": "interviewer", "content": "Q1", "day": DAY},
        {"role": "candidate", "content": "A1",
         "evaluation": AnswerEvaluation.model_validate(WRONG_GRADE).model_dump()},
    ]
    turns = turns_from_history(history)

    assert turns[0].evaluation is not None
    assert turns[0].evaluation.score == 0.15
    assert turns[0].evaluation.level is UnderstandingLevel.RECALL


def test_a_malformed_stored_grade_degrades_to_ungraded():
    history = [
        {"role": "interviewer", "content": "Q1", "day": DAY},
        {"role": "candidate", "content": "A1", "evaluation": {"score": "junk"}},
    ]
    assert turns_from_history(history)[0].evaluation is None


# ==========================================================================
# The directive the model sees
# ==========================================================================


def test_belief_probe_directive_does_not_call_a_good_answer_thin():
    from app.models import QuestionKind

    thin = prompts.render_directive(
        QuestionMode.FOLLOW_UP, QuestionKind.PROBE_DEPTH, "r"
    )
    held_up = prompts.render_directive(
        QuestionMode.FOLLOW_UP, QuestionKind.PROBE_DEPTH, "r", answer_was_thin=False
    )

    assert "That answer was thin" in thin, "default wording unchanged"
    assert "thin" not in held_up
    assert "held up" in held_up and "Do not move on" in held_up
