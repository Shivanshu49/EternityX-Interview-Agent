"""Feedback engine tests.

The load-bearing requirement is that feedback is graded on the same axis the
interview was built on -- the same Priority tiers and SignalPatterns the
question engine used -- rather than a second scoring system invented here.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import feedback_engine as fe
from app import question_engine as qe
from app.curriculum import CURRICULUM
from app.models import (
    Candidate,
    CohortSignals,
    DayTurn,
    Feedback,
    InterviewSession,
    Member,
    Mission,
    SignalPattern,
)


@pytest.fixture
def candidate() -> Candidate:
    return Candidate(
        member=Member(id="c-1", name="Priya"),
        missions=[
            Mission(day=15, title="LoRA lab", skipped=True),
            Mission(day=13, title="Function calling", passed=True, attempts=5),
            Mission(day=9, title="Vector DB", passed=True, attempts=1),
            Mission(day=26, title="Cost", passed=False, attempts=4),
        ],
        signals=CohortSignals(commitDays=26, missionsCompleted=28, missionsFirstTry=15),
    )


@pytest.fixture
def session(candidate) -> InterviewSession:
    return InterviewSession(
        candidate=candidate,
        turns=[
            DayTurn(day=15, question="Explain LoRA.", answer="LoRA freezes base weights."),
            DayTurn(day=13, question="Why 5 attempts?", answer="Dunno."),
            DayTurn(day=13, question="What broke?", answer="Schema.", follow_up=True),
            DayTurn(day=9, question="Index choice?", answer="ChromaDB with metadata filters."),
        ],
    )


class StubJSON:
    """Returns whatever object the caller's schema asks for."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {
            "summary": "s", "strengths": ["a"], "gaps": ["b"], "next": ["c"],
        }
        self.calls: list[dict] = []
        self.messages = self
        self.beta = SimpleNamespace(messages=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
            stop_reason="end_turn",
        )


# --------------------------------------------------------------------------
# Evidence uses the engine's own tiering
# --------------------------------------------------------------------------


def test_evidence_reuses_the_question_engines_tiers(session):
    entries = {e.day: e for e in fe.build_evidence(session, CURRICULUM)}

    for day, entry in entries.items():
        mission = session.candidate.mission_for(day)
        assert entry.tier == qe.priority_for(mission).name, "tier must come from Priority"
        assert entry.pattern == qe.classify(mission).value, "pattern must come from classify()"


def test_every_tier_and_pattern_is_a_known_enum_value(session):
    """No third vocabulary: the labels must be exactly the engine's own."""
    valid_tiers = {p.name for p in qe.Priority}
    valid_patterns = {p.value for p in SignalPattern}

    for entry in fe.build_evidence(session, CURRICULUM):
        assert entry.tier in valid_tiers
        assert entry.pattern in valid_patterns


def test_evidence_labels_match_the_cohort_record(session):
    entries = {e.day: e for e in fe.build_evidence(session, CURRICULUM)}

    assert entries[15].tier == "SKIPPED" and entries[15].pattern == "avoided"
    assert entries[13].tier == "STRUGGLED" and entries[13].pattern == "grinder"
    assert entries[9].tier == "VERIFY" and entries[9].pattern == "fluent"


def test_evidence_covers_only_days_the_interview_asked_about(session):
    days = {e.day for e in fe.build_evidence(session, CURRICULUM)}
    assert days == {15, 13, 9}, "day 26 was never asked; it must not be graded"


def test_evidence_groups_multiple_turns_and_counts_probes(session):
    entries = {e.day: e for e in fe.build_evidence(session, CURRICULUM)}
    assert len(entries[13].exchanges) == 2
    assert entries[13].probes == 1
    assert entries[13].thin_answers == 2, "both day-13 answers were thin"


def test_evidence_carries_real_curriculum_objectives(session):
    entries = {e.day: e for e in fe.build_evidence(session, CURRICULUM)}
    assert entries[9].objectives == CURRICULUM.get(9).objectives
    assert entries[9].title == CURRICULUM.get(9).title


# --------------------------------------------------------------------------
# Prompt grounding
# --------------------------------------------------------------------------


def test_prompt_carries_the_tiering_and_the_transcript(session):
    payload = fe.build_feedback_payload(session, CURRICULUM)
    body = payload["messages"][0]["content"]

    assert "tier SKIPPED" in body and "'avoided'" in body
    assert "tier VERIFY" in body and "'fluent'" in body
    assert "Skipped without attempting it." in body       # signals.evidence()
    assert "Needed 5 attempts" in body
    assert "ChromaDB with metadata filters." in body      # the actual answer
    assert "probe 1x here" in body
    assert payload["system"].startswith("You are a senior engineer")


def test_prompt_explains_what_each_tier_means():
    """The model is told how to read the labels, not just handed them."""
    for tier in ("SKIPPED", "STRUGGLED", "VERIFY", "ROUTINE"):
        assert tier in fe.FEEDBACK_SYSTEM_PROMPT


def test_empty_interview_is_stated_not_invented(candidate):
    payload = fe.build_feedback_payload(InterviewSession(candidate=candidate), CURRICULUM)
    assert "No questions were asked" in payload["messages"][0]["content"]


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def test_generate_feedback_returns_the_api_contract(session):
    client = StubJSON()
    result = fe.generate_feedback(session, CURRICULUM, client=client)

    assert isinstance(result, Feedback)
    assert result.model_dump().keys() == {"summary", "strengths", "gaps", "next"}


def test_generate_feedback_requests_structured_output(session):
    client = StubJSON()
    fe.generate_feedback(session, CURRICULUM, client=client)

    fmt = client.calls[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["required"] == ["summary", "strengths", "gaps", "next"]
    assert fmt["schema"]["additionalProperties"] is False


def test_generate_feedback_accepts_the_api_layers_dict_session(candidate):
    raw = {
        "candidate": candidate.model_dump(by_alias=True),
        "history": [
            {"role": "interviewer", "content": "Explain LoRA.", "day": 15},
            {"role": "candidate", "content": "It freezes base weights."},
        ],
        "days_covered": [15],
        "questions_asked": 1,
        "phase": "wrapping_up",
    }
    result = fe.generate_feedback(raw, CURRICULUM, client=StubJSON())
    assert isinstance(result, Feedback)


def test_generate_feedback_does_not_mutate_the_session(session):
    before = session.model_dump()
    fe.generate_feedback(session, CURRICULUM, client=StubJSON())
    assert session.model_dump() == before


def test_malformed_feedback_raises_rather_than_returning_junk(session):
    client = StubJSON({"summary": "s"})  # missing three required lists
    with pytest.raises(Exception):
        fe.generate_feedback(session, CURRICULUM, client=client)


def test_report_survives_an_endpoint_that_ignores_the_schema(session):
    """AgentRouter accepts `output_config` and drops it, so the reply is fenced.

    The deployed provider behaves exactly this way. If this regresses, every
    interview runs its eight questions and then 502s on the report.
    """

    class FencedStub:
        def __init__(self) -> None:
            self.messages = self
            self.beta = SimpleNamespace(messages=self)

        def create(self, **kwargs):
            body = json.dumps(
                {"summary": "s", "strengths": ["a"], "gaps": ["b"], "next": ["c"]}
            )
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=f"```json\n{body}\n```")],
                stop_reason="end_turn",
            )

    result = fe.generate_feedback(session, CURRICULUM, client=FencedStub())
    assert isinstance(result, Feedback)
    assert result.strengths == ["a"]


def test_report_generate_returns_a_plain_dict_for_routes(session):
    """routes.py validates report.generate()'s return into Feedback."""
    from app import report

    result = report.generate(
        {
            "candidate": session.candidate.model_dump(by_alias=True),
            "history": [{"role": "interviewer", "content": "Q", "day": 9},
                        {"role": "candidate", "content": "A"}],
            "days_covered": [9],
            "questions_asked": 1,
            "phase": "wrapping_up",
        }
    )
    assert isinstance(result, dict)
    assert set(result) == {"summary", "strengths", "gaps", "next"}
