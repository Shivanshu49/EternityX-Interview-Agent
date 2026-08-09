"""Shared Pydantic schemas.

These types are the contract between the question engine (A), the FastAPI
layer (B), and the frontend (C). Anything crossing a module boundary is
defined here so no one has to guess at a dict's shape.

The cohort is modelled the way the data actually arrives: 31 numbered days, each
with one mission the candidate either passed, ground through, or skipped. There
is deliberately no second, topic-shaped view of the same facts -- one shape, one
classifier, one selection path.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# --------------------------------------------------------------------------
# Inputs: the curriculum
# --------------------------------------------------------------------------


class CurriculumDay(BaseModel):
    """One day of the cohort, as defined in `curriculum.json`."""

    day: int = Field(ge=1, description="1-based day number")
    title: str
    type: str = Field("", description="e.g. 'concept', 'build', 'lab'")
    tools: list[str] = Field(default_factory=list)
    objectives: list[str] = Field(
        default_factory=list, description="What the candidate should be able to do"
    )


class CurriculumModule(BaseModel):
    """One of the cohort's 8 modules -- a named span of consecutive days."""

    n: int
    title: str
    days: list[int] = Field(
        default_factory=list,
        description="Inclusive [first_day, last_day] range, not a member list",
    )

    @property
    def day_range(self) -> frozenset[int]:
        """Expand [first, last] into the actual day numbers it covers."""
        if len(self.days) != 2:
            return frozenset(self.days)
        first, last = self.days
        return frozenset(range(first, last + 1))


class Curriculum(BaseModel):
    """The full 31-day syllabus, indexed by day number."""

    days: list[CurriculumDay] = Field(default_factory=list)
    cohort: str = Field("", description="Free-text label from the source file")
    modules: list[CurriculumModule] = Field(
        default_factory=list,
        description="Module grouping. Not used for selection, but it is where the "
        "flagship day bands come from -- see question_engine.FLAGSHIP_DAYS.",
    )

    @classmethod
    def load(cls, path: str | Path) -> Curriculum:
        """Read curriculum.json.

        Accepts either a bare list of days or `{"days": [...]}` so the file can
        be reshaped later without breaking callers.
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(days=raw) if isinstance(raw, list) else cls.model_validate(raw)

    def get(self, day: int) -> CurriculumDay | None:
        return next((d for d in self.days if d.day == day), None)

    def day_numbers(self) -> list[int]:
        return sorted(d.day for d in self.days)

    def module_for(self, day: int) -> CurriculumModule | None:
        """Which module a day belongs to, if the file declares modules."""
        return next((m for m in self.modules if day in m.day_range), None)

    def module_named(self, fragment: str) -> CurriculumModule | None:
        """First module whose title contains `fragment`, case-insensitively."""
        needle = fragment.lower()
        return next((m for m in self.modules if needle in m.title.lower()), None)

    def tool_vocabulary(self) -> frozenset[str]:
        """Every tool named anywhere in the syllabus, lowercased.

        Used as the concreteness dictionary when judging answer depth: naming a
        real tool counts as being specific even if it belongs to another day.
        """
        return frozenset(t.lower() for d in self.days for t in d.tools if t)

    def __len__(self) -> int:
        return len(self.days)


# --------------------------------------------------------------------------
# Inputs: the candidate's cohort record
# --------------------------------------------------------------------------


class Mission(BaseModel):
    """The candidate's outcome on one day's mission."""

    day: int = Field(ge=1)
    title: str = ""
    passed: bool = False
    attempts: int = Field(0, ge=0, description="Submissions made; 0 if never attempted")
    skipped: bool = False

    @property
    def has_record(self) -> bool:
        """Whether this row says anything at all.

        No attempts, no pass, no explicit skip means the mission was never
        started -- which is absence of data, not evidence of a gap, and the
        classifier and the ranker both need to treat it that way.
        """
        return self.skipped or self.passed or self.attempts > 0


class CohortSignals(BaseModel):
    """Cohort-wide totals. Context for the interviewer, not a selection input.

    JSON arrives camelCase; both spellings are accepted so the API layer can
    hand us the payload untouched.
    """

    model_config = ConfigDict(populate_by_name=True)

    commit_days: int = Field(0, ge=0, alias="commitDays")
    missions_completed: int = Field(0, ge=0, alias="missionsCompleted")
    missions_first_try: int = Field(0, ge=0, alias="missionsFirstTry")


class Member(BaseModel):
    """Who the candidate is. Shape is owned upstream, so unknown keys pass through.

    `job_role` and `years_experience` are declared rather than left to `extra`
    because the interviewer calibrates on them: the cohort runs from a intern
    with no professional experience to a distinguished engineer with nearly
    thirty years, and the same question does not serve both.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = None
    name: str | None = None
    email: str | None = None
    job_role: str | None = Field(None, alias="jobRole")
    years_experience: int | None = Field(None, alias="yearsExperience", ge=0)
    education: str | None = None


class Candidate(BaseModel):
    """One interviewee: who they are, and what they actually did for 31 days."""

    member: Member = Field(default_factory=Member)
    missions: list[Mission] = Field(default_factory=list)
    signals: CohortSignals = Field(default_factory=CohortSignals)

    def mission_for(self, day: int) -> Mission | None:
        return next((m for m in self.missions if m.day == day), None)

    @property
    def display_name(self) -> str:
        return self.member.name or self.member.id or "the candidate"


# --------------------------------------------------------------------------
# Derived: what the record says about the candidate
# --------------------------------------------------------------------------


class SignalPattern(str, Enum):
    """How a candidate behaved on one day. Drives interview strategy."""

    FLUENT = "fluent"          # first-try pass -- genuine command
    STEADY = "steady"          # passed in a couple of tries -- solid
    GRINDER = "grinder"        # passed only after many tries -- possibly shallow
    STRUGGLED = "struggled"    # attempted, never passed -- known gap
    AVOIDED = "avoided"        # skipped outright -- gap or low confidence
    UNKNOWN = "unknown"        # never started, or no row at all


class DayProfile(BaseModel):
    """The engine's belief about one day, before and during the interview."""

    day: int
    title: str = ""
    pattern: SignalPattern
    mastery: float = Field(ge=0.0, le=1.0, description="Believed skill level")
    uncertainty: float = Field(ge=0.0, le=1.0, description="How unsure we are")
    evidence: str = Field(description="Plain-language reason for this belief")
    attempts: int = 0
    passed: bool = False
    skipped: bool = False


# --------------------------------------------------------------------------
# Interview artifacts
# --------------------------------------------------------------------------


class QuestionKind(str, Enum):
    """Interview move. Chosen from the day's pattern and current belief."""

    PROBE_DEPTH = "probe_depth"    # they got it right -- do they know *why*?
    EDGE_CASE = "edge_case"        # push a fluent candidate to the boundary
    SCAFFOLD = "scaffold"          # start accessible, build confidence
    DIAGNOSE_GAP = "diagnose_gap"  # find where the understanding breaks
    APPLY = "apply"                # transfer the idea to a new situation
    CLARIFY = "clarify"            # last answer was ambiguous -- resolve it


class QuestionMode(str, Enum):
    """Whether the next question opens a new day or digs into the current one."""

    OPENING = "opening"
    FOLLOW_UP = "follow_up"


class UnderstandingLevel(str, Enum):
    NONE = "none"
    RECALL = "recall"              # repeats the definition
    APPLIED = "applied"            # can use it
    TRANSFERRED = "transferred"    # can adapt it to a new context


class AnswerEvaluation(BaseModel):
    """Structured grading of one answer. Produced by the LLM.

    This schema is handed to the structured-output API the same way `Feedback`
    is, so the descriptions steer the grader as well as validate its output.
    `extra="forbid"` puts `additionalProperties: false` in the emitted schema.
    """

    model_config = ConfigDict(extra="forbid")

    score: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How much real understanding the answer demonstrates. 0.0-0.2: wrong "
            "or nothing demonstrated. 0.3-0.45: fragments, or recites a "
            "definition without using it. Around 0.5: partially right but "
            "incomplete. 0.6-0.75: correct and applied to the situation. 0.85+: "
            "correct, and adapts the idea or names its limits unprompted. Length, "
            "keyword density and confidence are not understanding."
        ),
    )
    level: UnderstandingLevel = Field(
        description=(
            "none: no relevant understanding shown. recall: repeats facts or "
            "definitions. applied: uses the concept correctly on the situation "
            "asked about. transferred: adapts it to new conditions or reasons "
            "about where it breaks."
        )
    )
    reasoning: str = Field(
        min_length=1,
        description=(
            "One or two sentences citing what the candidate actually said and "
            "how it does or does not meet the day's objectives."
        ),
    )
    strengths: list[str] = Field(
        default_factory=list,
        description="Specific things the answer got right. Empty if none.",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Specific things missing or wrong. Empty if none.",
    )
    needs_follow_up: bool = Field(
        False,
        description=(
            "True when one more pointed question would settle real doubt: the "
            "answer was ambiguous, or close but incomplete. False when the "
            "answer is clearly strong or clearly hopeless, where moving on "
            "teaches more."
        ),
    )


class DayTurn(BaseModel):
    """One question/answer exchange, anchored to the curriculum day it probed."""

    day: int
    question: str
    answer: str = ""
    follow_up: bool = Field(
        False, description="True if this probed deeper on the previous turn's day"
    )
    evaluation: AnswerEvaluation | None = Field(
        None, description="Set by the grader, if one has run. Overrides heuristics."
    )


class InterviewSession(BaseModel):
    """Everything `next_question` needs. Passed in, never held globally."""

    candidate: Candidate
    turns: list[DayTurn] = Field(default_factory=list)
    max_questions: int = Field(8, ge=1)

    @property
    def asked_count(self) -> int:
        return len(self.turns)

    @property
    def days_covered(self) -> list[int]:
        """Distinct days already probed, in the order they were first asked."""
        seen: list[int] = []
        for turn in self.turns:
            if turn.day not in seen:
                seen.append(turn.day)
        return seen

    @property
    def last_turn(self) -> DayTurn | None:
        return self.turns[-1] if self.turns else None

    def follow_ups_on(self, day: int) -> int:
        """How many consecutive follow-ups the tail of the interview has spent on `day`."""
        count = 0
        for turn in reversed(self.turns):
            if turn.day != day:
                break
            if turn.follow_up:
                count += 1
        return count


# --------------------------------------------------------------------------
# Output: the structured report
# --------------------------------------------------------------------------


class DayVerdict(BaseModel):
    day: int
    title: str = ""
    signal_pattern: SignalPattern
    prior_mastery: float
    final_mastery: float
    verdict: str = Field(description="What the interview revealed about this day")


class InterviewReport(BaseModel):
    candidate_id: str
    candidate_name: str | None = None
    summary: str
    day_verdicts: list[DayVerdict] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    growth_areas: list[str] = Field(default_factory=list)
    recommendation: str


# ==========================================================================
# API boundary (owner B)
# ==========================================================================
#
# The HTTP contract, kept verbatim from B's implementation. `QuestionResult`
# is the shape `routes._next_question` validates the engine's return value
# into; `NextQuestion` in question_engine.py is deliberately named to satisfy
# it attribute-for-attribute via `from_attributes=True`.


class InterviewRequest(BaseModel):
    """Request accepted by the single interview endpoint."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: str = Field(alias="sessionId", min_length=1)
    candidate: dict[str, Any] | None = None
    message: str | None = None

    @field_validator("session_id", "message")
    @classmethod
    def reject_blank_strings(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class QuestionResult(BaseModel):
    """Contract returned by the question engine.

    Every field the trace reports is declared here rather than left to `extra`.
    With `from_attributes=True` pydantic reads only declared fields off an
    object, so an undeclared one is silently dropped when the engine returns a
    `NextQuestion` and silently kept when a test hands over a dict -- which is
    exactly the kind of difference that passes tests and fails in production.
    """

    model_config = ConfigDict(extra="allow", from_attributes=True)

    reply: str = Field(min_length=1)
    day: int = Field(ge=1, le=31)
    tier: str | None = None
    pattern: str | None = None
    reason: str | None = None
    is_follow_up: bool | None = None
    day_title: str | None = None
    mode: str | None = None
    move: str | None = None
    last_evaluation: AnswerEvaluation | None = Field(
        None,
        description=(
            "Grade for the answer that preceded this question, when the "
            "adaptive-eval path produced one. The API layer stores it on the "
            "candidate's history entry; it never appears in the HTTP response."
        ),
    )


class Feedback(BaseModel):
    """Required structured feedback format.

    This schema is handed to the structured-output API, so the constraints and
    descriptions below steer generation as well as validate the result. Before
    they were added, `{"summary": "", "strengths": [], "gaps": [], "next": []}`
    validated cleanly, returned HTTP 200, and rendered as a report card with a
    heading and nothing underneath it -- a silent failure at the one moment the
    candidate is actually reading.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        min_length=1,
        description=(
            "Two to four sentences on how the candidate came across overall. "
            "Ground it in what they actually said, not in their cohort record."
        ),
    )
    strengths: list[str] = Field(
        min_length=1,
        description=(
            "What they demonstrated, each tied to a specific thing they said. "
            "'Explained why chunk overlap fixes a straddled answer', not "
            "'strong on retrieval'."
        ),
    )
    gaps: list[str] = Field(
        min_length=1,
        description=(
            "Where their understanding ran out, naming the topic and what was "
            "missing. If the interview was too short to judge something, say so "
            "rather than inventing a gap."
        ),
    )
    next: list[str] = Field(
        min_length=1,
        description=(
            "Concrete next actions the candidate can start on, each pointing at "
            "a specific curriculum day or a specific thing to build or measure."
        ),
    )


class QuestionTrace(BaseModel):
    """Why the engine asked what it asked.

    The selection reasoning is computed on every turn and was previously
    discarded before the response was built, which made a genuinely adaptive
    engine indistinguishable from a chat wrapper. It is opt-in via `?explain=1`
    so the response the specification defines is unchanged by default.
    """

    day: int
    day_title: str = ""
    tier: str = Field(description="Priority tier, e.g. 'SKIPPED'")
    pattern: str = Field(description="Cohort signal, e.g. 'avoided'")
    move: str = Field(description="Interview tactic, e.g. 'scaffold'")
    reason: str = Field(description="Plain-language justification for this day")
    is_follow_up: bool = False
    questions_asked: int = 0
    days_covered: list[int] = Field(default_factory=list)


class InterviewResponse(BaseModel):
    """Successful response from the interview endpoint."""

    reply: str
    done: bool
    feedback: Feedback | None = None
    trace: QuestionTrace | None = None


# --------------------------------------------------------------------------
# Adapter: B's dict session -> the engine's typed session
# --------------------------------------------------------------------------
#
# The API layer stores sessions as plain dicts (see `app/session_store.py`) and
# owns both mutation and the completion gate. The engine works in typed models
# and mutates nothing. This function is the seam between the two.

# B's `can_finish` ends an interview at >=8 questions AND >=4 distinct days, so
# the engine must not impose a lower ceiling of its own -- returning None while
# the API still wants a question would surface as a 502. One question per
# curriculum day is the natural upper bound.
DICT_SESSION_MAX_QUESTIONS = 31


def turns_from_history(history: list[dict[str, Any]]) -> list[DayTurn]:
    """Fold B's flat `history` list into question/answer pairs.

    History alternates interviewer and candidate entries. An interviewer entry
    opens a turn; the next candidate entry closes it. A trailing interviewer
    entry becomes a turn with an empty answer, which is correct -- the question
    was asked but not yet answered.
    """
    turns: list[DayTurn] = []
    for entry in history:
        if entry.get("role") == "interviewer":
            turns.append(
                DayTurn(
                    day=int(entry.get("day", 0) or 0),
                    question=str(entry.get("content", "")),
                    follow_up=bool(entry.get("is_follow_up", False)),
                )
            )
        elif entry.get("role") == "candidate" and turns:
            turns[-1].answer = str(entry.get("content", ""))
            # A grade recorded by an earlier turn. Restored so beliefs can be
            # folded over the whole interview, not just the newest answer.
            stored = entry.get("evaluation")
            if stored is not None:
                try:
                    turns[-1].evaluation = AnswerEvaluation.model_validate(stored)
                except ValidationError:
                    # A malformed stored grade must degrade to "ungraded", not
                    # take the interview down with it.
                    pass
    return [t for t in turns if t.day >= 1]


def session_from_dict(session: dict[str, Any]) -> InterviewSession:
    """Build a typed `InterviewSession` from the API layer's dict."""
    return InterviewSession(
        candidate=Candidate.model_validate(session.get("candidate") or {}),
        turns=turns_from_history(session.get("history") or []),
        max_questions=int(
            session.get("max_questions") or DICT_SESSION_MAX_QUESTIONS
        ),
    )


def coerce_session(session: InterviewSession | dict[str, Any]) -> InterviewSession:
    """Accept either shape, so the engine works from tests and from the API."""
    return session if isinstance(session, InterviewSession) else session_from_dict(session)


def coerce_curriculum(curriculum: Curriculum | dict[str, Any] | None) -> Curriculum | None:
    """Accept the typed model or the raw dict `app/curriculum.py` loads."""
    if curriculum is None or isinstance(curriculum, Curriculum):
        return curriculum
    return Curriculum.model_validate(curriculum)
