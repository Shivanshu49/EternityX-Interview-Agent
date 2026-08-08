"""Tests for the deterministic half of the engine: day selection and answer depth.

The LLM call is exercised through a stub, so the whole suite runs offline.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import question_engine as qe
from app.curriculum import CURRICULUM_PATH
from app.models import (
    AnswerEvaluation,
    Candidate,
    Curriculum,
    CurriculumDay,
    DayTurn,
    InterviewSession,
    Mission,
    QuestionMode,
    SignalPattern,
    UnderstandingLevel,
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def curriculum() -> Curriculum:
    """A minimal stand-in for the real 31 days, with both flagship bands present."""
    return Curriculum(
        days=[
            CurriculumDay(
                day=d,
                title=f"Day {d}",
                type="build",
                tools=["pgvector"] if d in qe.VECTOR_DAYS else ["MCP Python SDK"],
                objectives=[f"objective for day {d}"],
            )
            for d in range(1, 32)
        ]
    )


def candidate_with(*missions: Mission) -> Candidate:
    return Candidate(missions=list(missions))


class StubMessages:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.text)],
            stop_reason="end_turn",
        )


class StubClient:
    def __init__(self, text: str = "One question?") -> None:
        self.messages = StubMessages(text)
        self.beta = SimpleNamespace(messages=self.messages)


# --------------------------------------------------------------------------
# Priority ordering
# --------------------------------------------------------------------------


def test_skipped_mission_outranks_everything(curriculum):
    candidate = candidate_with(
        Mission(day=2, passed=True, attempts=1),
        Mission(day=5, passed=True, attempts=9),   # heavy grinder
        Mission(day=6, skipped=True),
    )
    pick = qe.pick_next_day(candidate, [], curriculum)
    assert pick.day == 6
    assert pick.priority is qe.Priority.SKIPPED
    assert pick.pattern is SignalPattern.AVOIDED


def test_struggle_outranks_first_try_pass(curriculum):
    candidate = candidate_with(
        Mission(day=9, passed=True, attempts=1),   # flagship first-try -> VERIFY
        Mission(day=12, passed=True, attempts=4),  # >2 attempts -> STRUGGLED
    )
    assert qe.pick_next_day(candidate, [], curriculum).day == 12


def test_attempted_but_never_passed_is_a_struggle():
    """Two failed attempts is a known gap even though it is not >2 attempts."""
    assert qe.priority_for(Mission(day=4, passed=False, attempts=2)) is qe.Priority.STRUGGLED


def test_first_try_pass_only_gets_verified_on_high_value_days():
    assert qe.priority_for(Mission(day=9, passed=True, attempts=1)) is qe.Priority.VERIFY
    assert qe.priority_for(Mission(day=2, passed=True, attempts=1)) is qe.Priority.ROUTINE


def test_within_a_tier_more_attempts_comes_first(curriculum):
    candidate = candidate_with(
        Mission(day=11, passed=True, attempts=3),
        Mission(day=12, passed=True, attempts=7),
    )
    assert qe.pick_next_day(candidate, [], curriculum).day == 12


def test_missing_mission_row_ranks_last(curriculum):
    candidate = candidate_with(Mission(day=2, passed=True, attempts=2))
    # Day 2 has a record; every other curriculum day does not.
    assert qe.pick_next_day(candidate, [], curriculum).day == 2


def test_selection_is_deterministic(curriculum):
    candidate = candidate_with(
        Mission(day=3, skipped=True),
        Mission(day=26, skipped=True),
        Mission(day=12, passed=True, attempts=5),
    )
    runs = [qe.pick_next_day(candidate, [], curriculum).day for _ in range(5)]
    assert len(set(runs)) == 1


# --------------------------------------------------------------------------
# Rule (d): flagship coverage
# --------------------------------------------------------------------------


def test_flagship_coverage_is_forced_by_the_fourth_pick(curriculum):
    """Three non-flagship days covered -> the fourth pick must be flagship."""
    candidate = candidate_with(
        Mission(day=2, skipped=True),
        Mission(day=3, skipped=True),
        Mission(day=5, skipped=True),
        Mission(day=6, skipped=True),   # a fourth skip that would otherwise win
        Mission(day=22, passed=True, attempts=1),
    )
    pick = qe.pick_next_day(candidate, [2, 3, 5], curriculum)
    assert pick.day in qe.FLAGSHIP_DAYS
    assert pick.forced_coverage is True
    assert "coverage" in pick.reason.lower()


def test_coverage_rule_stays_on_until_satisfied(curriculum):
    candidate = candidate_with(Mission(day=2, skipped=True), Mission(day=6, skipped=True))
    assert qe.pick_next_day(candidate, [1, 2, 3, 4, 5], curriculum).day in qe.FLAGSHIP_DAYS


def test_coverage_rule_does_not_fire_once_a_flagship_day_is_covered(curriculum):
    candidate = candidate_with(
        Mission(day=6, skipped=True),
        Mission(day=22, passed=True, attempts=1),
    )
    pick = qe.pick_next_day(candidate, [1, 2, 22], curriculum)
    assert pick.forced_coverage is False
    assert pick.day == 6


def test_coverage_rule_does_not_fire_early(curriculum):
    candidate = candidate_with(
        Mission(day=6, skipped=True),
        Mission(day=9, passed=True, attempts=1),
    )
    assert qe.pick_next_day(candidate, [1], curriculum).forced_coverage is False


def test_coverage_rule_can_reach_a_day_with_no_mission_row(curriculum):
    """A candidate who never touched a flagship day still gets asked about one."""
    candidate = candidate_with(
        Mission(day=2, skipped=True),
        Mission(day=3, skipped=True),
        Mission(day=4, skipped=True),
        Mission(day=5, skipped=True),
    )
    pick = qe.pick_next_day(candidate, [2, 3, 4], curriculum)
    assert pick.day in qe.FLAGSHIP_DAYS
    assert pick.priority is qe.Priority.NO_DATA


# --------------------------------------------------------------------------
# Pool boundaries
# --------------------------------------------------------------------------


def test_without_a_curriculum_only_mission_days_are_candidates():
    candidate = candidate_with(Mission(day=7, skipped=True), Mission(day=8, passed=True))
    assert qe.pick_next_day(candidate, [], None).day == 7


def test_returns_none_when_every_day_is_covered():
    candidate = candidate_with(Mission(day=7, skipped=True))
    assert qe.pick_next_day(candidate, [7], None) is None


def test_returns_none_for_an_empty_candidate():
    assert qe.pick_next_day(Candidate(), [], None) is None


# --------------------------------------------------------------------------
# Answer depth
# --------------------------------------------------------------------------

LONG_ANSWER = " ".join(["word"] * 60)


@pytest.mark.parametrize(
    "answer",
    ["", "   ", "I don't know", "Not sure, we skipped that one.", "Vector stuff mostly."],
)
def test_thin_answers_are_shallow(answer):
    assert qe.is_shallow(answer) is True


def test_long_answers_are_not_shallow():
    assert qe.is_shallow(LONG_ANSWER) is False


def test_a_long_non_answer_is_still_shallow():
    assert qe.is_shallow("I don't know. " + LONG_ANSWER) is True


def test_middling_answer_needs_concrete_anchors(curriculum):
    vague = "We looked at the retrieval part and it mostly worked well enough for us."
    concrete = "We used pgvector and cut chunk size to 400 tokens, which fixed recall."
    vocab = curriculum.tool_vocabulary()
    assert qe.is_shallow(vague, vocabulary=vocab) is True
    assert qe.is_shallow(concrete, vocabulary=vocab) is False


def test_grader_verdict_overrides_the_heuristic():
    turn = DayTurn(
        day=9,
        question="q",
        answer=LONG_ANSWER,  # heuristic alone would say this is fine
        evaluation=AnswerEvaluation(
            score=0.2,
            level=UnderstandingLevel.RECALL,
            reasoning="recited a definition",
            needs_follow_up=True,
        ),
    )
    assert qe.needs_follow_up(turn) is True


def test_strong_grader_verdict_moves_on():
    turn = DayTurn(
        day=9,
        question="q",
        answer="short",  # heuristic alone would probe
        evaluation=AnswerEvaluation(
            score=0.9,
            level=UnderstandingLevel.TRANSFERRED,
            reasoning="nailed it",
            needs_follow_up=False,
        ),
    )
    assert qe.needs_follow_up(turn) is False


# --------------------------------------------------------------------------
# Turn resolution
# --------------------------------------------------------------------------


def session_with(candidate: Candidate, *turns: DayTurn, max_questions: int = 8):
    return InterviewSession(candidate=candidate, turns=list(turns), max_questions=max_questions)


def test_shallow_answer_probes_the_same_day(curriculum):
    candidate = candidate_with(Mission(day=6, skipped=True), Mission(day=9, skipped=True))
    session = session_with(candidate, DayTurn(day=6, question="q", answer="Not sure."))

    target = qe.resolve_target(session, curriculum)
    assert target.day == 6
    assert target.mode is QuestionMode.FOLLOW_UP


def test_good_answer_moves_to_a_new_day(curriculum):
    candidate = candidate_with(Mission(day=6, skipped=True), Mission(day=9, skipped=True))
    session = session_with(candidate, DayTurn(day=6, question="q", answer=LONG_ANSWER))

    target = qe.resolve_target(session, curriculum)
    assert target.day != 6
    assert target.mode is QuestionMode.OPENING


def test_follow_ups_are_capped_per_day(curriculum):
    candidate = candidate_with(Mission(day=6, skipped=True), Mission(day=9, skipped=True))
    session = session_with(
        candidate,
        DayTurn(day=6, question="q1", answer="Not sure."),
        DayTurn(day=6, question="q2", answer="Still not sure.", follow_up=True),
        DayTurn(day=6, question="q3", answer="No idea.", follow_up=True),
    )
    target = qe.resolve_target(session, curriculum)
    assert target.day != 6, "should stop ratholing after MAX_FOLLOW_UPS_PER_DAY"


def test_unanswered_turn_does_not_trigger_a_follow_up(curriculum):
    """A question we just asked but have no answer for is not evidence of anything."""
    candidate = candidate_with(Mission(day=6, skipped=True), Mission(day=9, skipped=True))
    session = session_with(candidate, DayTurn(day=6, question="q", answer=""))
    assert qe.resolve_target(session, curriculum).mode is QuestionMode.OPENING


def test_interview_ends_at_max_questions(curriculum):
    candidate = candidate_with(Mission(day=6, skipped=True), Mission(day=9, skipped=True))
    session = session_with(
        candidate,
        DayTurn(day=6, question="q", answer=LONG_ANSWER),
        max_questions=1,
    )
    assert qe.resolve_target(session, curriculum) is None
    assert qe.next_question(session, curriculum, client=StubClient()) is None


# --------------------------------------------------------------------------
# next_question + payload shape
# --------------------------------------------------------------------------


def test_next_question_returns_the_model_text_and_does_not_mutate(curriculum):
    candidate = candidate_with(Mission(day=22, skipped=True))
    session = session_with(candidate)

    question = qe.next_question(session, curriculum, client=StubClient("Why MCP?"))

    assert question.reply == "Why MCP?"
    assert question.day == 22
    assert question.mode is QuestionMode.OPENING
    assert session.turns == [], "engine must leave session state to the caller"


def test_payload_starts_with_a_user_turn_and_ends_with_the_brief(curriculum):
    candidate = candidate_with(Mission(day=22, skipped=True), Mission(day=9, skipped=True))
    session = session_with(candidate, DayTurn(day=9, question="earlier q", answer="Not sure."))
    client = StubClient()

    qe.next_question(session, curriculum, client=client)
    payload = client.messages.calls[0]

    assert payload["messages"][0]["role"] == "user"
    assert payload["messages"][-1]["role"] == "user"
    assert payload["system"].startswith("You are a senior engineer")
    # History is replayed as real dialogue, so the earlier question is an assistant turn.
    assert {"role": "assistant", "content": "earlier q"} in payload["messages"]
    # The final brief carries the day's objectives and the candidate's record.
    assert "Day 9" in payload["messages"][-1]["content"]
    assert "skipped" in payload["messages"][-1]["content"].lower()


def test_payload_roles_are_valid_for_the_messages_api(curriculum):
    """First message must be a user turn; every role must be user or assistant."""
    candidate = candidate_with(*(Mission(day=d, skipped=True) for d in range(1, 10)))
    session = session_with(
        candidate,
        *(DayTurn(day=d, question=f"q{d}", answer="Not sure.") for d in range(1, 6)),
    )
    client = StubClient()
    qe.next_question(session, curriculum, client=client)

    roles = [m["role"] for m in client.messages.calls[0]["messages"]]
    assert roles[0] == "user"
    assert set(roles) <= {"user", "assistant"}


# --------------------------------------------------------------------------
# signals.classify against the single shape
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mission,expected",
    [
        (Mission(day=1, passed=True, attempts=1), SignalPattern.FLUENT),
        (Mission(day=1, passed=True, attempts=0), SignalPattern.FLUENT),
        (Mission(day=1, passed=True, attempts=2), SignalPattern.STEADY),
        (Mission(day=1, passed=True, attempts=3), SignalPattern.GRINDER),
        (Mission(day=1, passed=True, attempts=9), SignalPattern.GRINDER),
        (Mission(day=1, passed=False, attempts=4), SignalPattern.STRUGGLED),
        (Mission(day=1, passed=False, attempts=1), SignalPattern.STRUGGLED),
        (Mission(day=1, skipped=True), SignalPattern.AVOIDED),
        (Mission(day=1, skipped=True, attempts=2), SignalPattern.AVOIDED),
        (Mission(day=1), SignalPattern.UNKNOWN),  # never started
        (None, SignalPattern.UNKNOWN),            # no row at all
    ],
)
def test_classify_covers_every_mission_shape(mission, expected):
    from app.signals import classify

    assert classify(mission) is expected


def test_never_started_is_unknown_not_struggled():
    """Zero attempts is absence of evidence, not evidence of a gap."""
    from app.signals import classify

    blank = Mission(day=4)
    assert blank.has_record is False
    assert classify(blank) is SignalPattern.UNKNOWN
    assert qe.priority_for(blank) is qe.Priority.NO_DATA


def test_grinder_threshold_is_shared_with_the_engine():
    """One number, defined once, so the tiers cannot drift apart."""
    from app.signals import GRINDER_ATTEMPT_THRESHOLD

    assert qe.STRUGGLE_ATTEMPTS == GRINDER_ATTEMPT_THRESHOLD


def test_build_profiles_keys_by_day_and_carries_titles(curriculum):
    from app.signals import build_profiles

    candidate = candidate_with(
        Mission(day=9, passed=True, attempts=1),
        Mission(day=22, skipped=True),
    )
    profiles = build_profiles(candidate, curriculum)

    assert set(profiles) == {9, 22}
    assert profiles[9].pattern is SignalPattern.FLUENT
    assert profiles[22].pattern is SignalPattern.AVOIDED
    assert profiles[22].title == "Day 22"
    # Avoided days carry the highest uncertainty, so they attract questions.
    assert profiles[22].uncertainty > profiles[9].uncertainty


def test_update_belief_converges_and_shrinks_uncertainty(curriculum):
    from app.signals import build_profiles, update_belief

    candidate = candidate_with(Mission(day=22, skipped=True))
    before = build_profiles(candidate, curriculum)[22]
    after = update_belief(before, score=0.9)

    assert after.mastery > before.mastery
    assert after.uncertainty < before.uncertainty


# --------------------------------------------------------------------------
# Regression: admitting you skipped something is not the same as not knowing it
# --------------------------------------------------------------------------


def test_admitting_a_skip_does_not_by_itself_make_an_answer_shallow(curriculum):
    """Tier (a) asks about skipped missions, so this phrasing is the common case."""
    answer = (
        "I skipped the hands-on lab, but I get the idea. LoRA freezes the base weights "
        "and trains a pair of low-rank adapter matrices, so you're only updating maybe "
        "1 percent of the parameters. QLoRA quantizes the frozen base to 4-bit on top "
        "of that so the whole thing fits on one consumer GPU."
    )
    assert qe.is_shallow(answer, vocabulary=curriculum.tool_vocabulary()) is False


def test_a_bare_skip_admission_is_still_shallow():
    """Without substance behind it, length catches what the phrase list no longer does."""
    assert qe.is_shallow("I skipped that one.") is True
    assert qe.is_shallow("We never used it.") is True


def test_disclaiming_knowledge_still_dominates():
    assert qe.is_shallow("I don't know. " + LONG_ANSWER) is True
    assert qe.is_shallow("No clue, sorry.") is True


def test_specificity_sees_acronyms_and_alphanumerics(curriculum):
    """HNSW, p95 and 200ms are as concrete as an engineer gets -- they must count."""
    answer = (
        "I used an HNSW index. Raising ef_search improves recall but costs latency, "
        "so I tuned it until p95 stayed under 200ms."
    )
    assert len(answer.split()) < qe.SUBSTANTIAL_WORDS, "must be judged on specificity"
    assert qe.is_shallow(answer, vocabulary=curriculum.tool_vocabulary()) is False


def test_vague_answers_of_the_same_length_are_still_shallow():
    vague = (
        "I used the index thing. Turning it up made the results better but slower, "
        "so I tuned it until it felt about right and then left it alone."
    )
    assert qe.is_shallow(vague) is True


# --------------------------------------------------------------------------
# Tool-name matching: whole words and whole phrases only
# --------------------------------------------------------------------------

# Names lifted from the real curriculum, chosen because each is a substring of
# an ordinary English word or of another tool in the same vocabulary.
REAL_VOCAB = frozenset(
    {
        "cline", "git", "github", "react", "vite", "sql", "sqlalchemy",
        "llm", "llms", "lora", "qlora", "transformers", "sentence transformers",
        "openai", "openai function calling", "pydantic", "chromadb",
        "python", "python-docx",
    }
)


def anchors(answer: str) -> set[str]:
    a = qe.specificity_anchors(answer, None, REAL_VOCAB)
    return a["tools"] | a["tokens"]


@pytest.mark.parametrize(
    "answer,ghost",
    [
        ("I had to decline the invite.", "cline"),
        ("There was an inclined surface.", "cline"),
        ("It was a legitimate bug in one digit.", "git"),
        ("The reaction time was awful.", "react"),
        ("We had to invite the reviewer.", "vite"),
    ],
)
def test_tool_names_do_not_match_inside_other_words(answer, ghost):
    assert ghost not in anchors(answer), f"{ghost!r} matched mid-word in {answer!r}"


def test_a_longer_tool_name_absorbs_the_shorter_one_it_contains():
    """"Sentence Transformers" is one tool, not also a mention of "Transformers"."""
    found = qe.specificity_anchors("We used Sentence Transformers.", None, REAL_VOCAB)
    assert "sentence transformers" in found["tools"]
    assert "transformers" not in found["tools"]


def test_containment_not_length_decides_absorption():
    """LoRA and QLoRA both survive -- neither contains the other at a boundary."""
    found = anchors("LoRA freezes the base weights and QLoRA quantizes it to 4-bit.")
    assert {"lora", "qlora"} <= found


def test_a_token_that_is_part_of_a_credited_tool_is_not_counted_twice():
    """"OpenAI Function Calling" should score once, not once more for "OpenAI"."""
    found = qe.specificity_anchors(
        "OpenAI Function Calling validated it.", None, REAL_VOCAB
    )
    assert "openai function calling" in found["tools"]
    assert "openai" not in found["tokens"], "camelCase token duplicates the tool hit"
    assert found["tools"].isdisjoint(found["tokens"])


def test_distinctly_named_tools_still_score_separately():
    found = anchors("I wired OpenAI Function Calling up to ChromaDB.")
    assert {"openai function calling", "chromadb"} <= found


def test_punctuated_tool_names_still_match():
    assert "python-docx" in anchors("I generated the report with python-docx.")


def test_substring_fix_does_not_deflate_a_genuinely_specific_answer(curriculum):
    """The turn-6 case: under the length threshold, so anchors must still carry it."""
    answer = (
        "I used ChromaDB with Sentence Transformers. The bug I hit was embedding "
        "queries with a different model than the documents, so scores were garbage "
        "until both used all-MiniLM-L6-v2."
    )
    vocab = Curriculum.load(CURRICULUM_PATH).tool_vocabulary()
    assert len(answer.split()) < qe.SUBSTANTIAL_WORDS
    assert qe.is_shallow(answer, vocabulary=vocab) is False


# --------------------------------------------------------------------------
# Integration seams with the API layer (owner B)
# --------------------------------------------------------------------------


def test_engine_accepts_the_api_layers_dict_session(curriculum):
    """routes.py passes a plain dict from session_store, not a typed model."""
    raw = {
        "candidate": {
            "member": {"name": "Test"},
            "missions": [{"day": 22, "title": "MCP", "skipped": True}],
            "signals": {"commitDays": 25, "missionsCompleted": 28, "missionsFirstTry": 15},
        },
        "history": [],
        "days_covered": [],
        "questions_asked": 0,
        "phase": "questioning",
    }
    result = qe.next_question(raw, curriculum, client=StubClient("Why MCP?"))
    assert result.day == 22
    assert result.reply == "Why MCP?"


def test_engine_accepts_a_raw_dict_curriculum():
    """app/curriculum.py could hand over either shape; both must work."""
    raw_curriculum = {"days": [{"day": 22, "title": "MCP", "tools": [], "objectives": []}]}
    session = session_with(candidate_with(Mission(day=22, skipped=True)))
    assert qe.next_question(session, raw_curriculum, client=StubClient()).day == 22


def test_result_validates_into_the_api_contract(curriculum):
    """`routes._next_question` does exactly this -- it must not raise."""
    from app.models import QuestionResult

    session = session_with(candidate_with(Mission(day=22, skipped=True)))
    result = qe.next_question(session, curriculum, client=StubClient("Q?"))
    contract = QuestionResult.model_validate(result)

    assert contract.reply == "Q?"
    assert contract.day == 22
    assert contract.tier == "SKIPPED"
    assert contract.pattern == "avoided"
    assert contract.is_follow_up is False
    assert contract.reason


def test_history_round_trips_through_the_dict_adapter(curriculum):
    """What routes._record_question writes must read back as the same turns."""
    from app.models import turns_from_history

    history = [
        {"role": "interviewer", "content": "Q1", "day": 7, "is_follow_up": False},
        {"role": "candidate", "content": "A1"},
        {"role": "interviewer", "content": "Q2", "day": 7, "is_follow_up": True},
        {"role": "candidate", "content": "A2"},
    ]
    turns = turns_from_history(history)
    assert [(t.day, t.question, t.answer, t.follow_up) for t in turns] == [
        (7, "Q1", "A1", False),
        (7, "Q2", "A2", True),
    ]


def test_follow_up_budget_tightens_while_breadth_is_owed():
    assert qe.follow_up_budget(0) == 1
    assert qe.follow_up_budget(qe.MIN_DISTINCT_DAYS - 1) == 1
    assert qe.follow_up_budget(qe.MIN_DISTINCT_DAYS) == qe.MAX_FOLLOW_UPS_PER_DAY


def test_probing_never_starves_the_completion_gate(curriculum):
    """A candidate answering everything in three words must still reach 4 days.

    Regression for the merge: unbounded probing meant 8 questions covered only
    ~3 distinct days, so `session_store.can_finish` could never fire.
    """
    candidate = candidate_with(*(Mission(day=d, skipped=True) for d in range(1, 32)))
    session = session_with(candidate, max_questions=8)

    for _ in range(8):
        result = qe.next_question(session, curriculum, client=StubClient("Q?"))
        assert result is not None
        session.turns.append(
            DayTurn(
                day=result.day,
                question=result.reply,
                answer="Not sure.",
                follow_up=result.is_follow_up,
            )
        )

    assert len(set(session.days_covered)) >= qe.MIN_DISTINCT_DAYS
