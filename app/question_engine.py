"""Adaptive question generation engine. Owner: A.

Two public entry points:

* `pick_next_day(candidate, days_covered)` -- which curriculum day to probe next,
  decided from the candidate's real mission record. Deterministic, no LLM.
* `next_question(session, curriculum)` -- builds the prompt for that day and asks
  the LLM for exactly one question, or a follow-up if the last answer was thin.

Both are pure functions of their arguments. Nothing here holds session state, and
nothing reads a global except the lazily-built LLM client, which callers can
replace by passing `client=`.
"""

from __future__ import annotations

import re
from enum import IntEnum
from functools import lru_cache

from pydantic import BaseModel, Field

from app import llm, prompts
from app.models import (
    Candidate,
    Curriculum,
    CurriculumDay,
    DayTurn,
    InterviewSession,
    Mission,
    QuestionKind,
    QuestionMode,
    SignalPattern,
    UnderstandingLevel,
)
from app.signals import GRINDER_ATTEMPT_THRESHOLD, classify, opening_move

# --------------------------------------------------------------------------
# Curriculum bands
# --------------------------------------------------------------------------
#
# Two stretches of the cohort carry most of the hiring signal, so the interview
# guarantees at least one of them gets covered even if the candidate's record
# points elsewhere. They double as the "high-value" set: a first-try pass here is
# worth verifying, because these are the days where luck and understanding look
# most alike from the outside.

AGENTIC_DAYS = frozenset(range(21, 25))  # Agentic AI / MCP
VECTOR_DAYS = frozenset(range(7, 11))    # Embeddings / Vector search
FLAGSHIP_DAYS = AGENTIC_DAYS | VECTOR_DAYS
HIGH_VALUE_DAYS = FLAGSHIP_DAYS

# By the Nth distinct day we probe, one flagship day must be among them.
COVERAGE_DEADLINE = 4

# "attempts > 2" from the spec. Same number that makes a pass a GRINDER, so it
# is imported rather than restated.
STRUGGLE_ATTEMPTS = GRINDER_ATTEMPT_THRESHOLD

# Cap on consecutive probes into one day, so a candidate who keeps giving thin
# answers does not burn the whole interview on a single topic.
MAX_FOLLOW_UPS_PER_DAY = 2


class Priority(IntEnum):
    """Selection tiers, best signal first. Lower sorts earlier.

    SKIPPED and STRUGGLED come first because those are the days where the cohort
    data genuinely does not tell us whether the candidate understands the
    material -- which makes them the days worth spending a question on.
    """

    SKIPPED = 0    # (a) avoided it entirely -- ask them to explain it anyway
    STRUGGLED = 1  # (b) >2 attempts, or attempted and never passed
    VERIFY = 2     # (c) first-try pass on a high-value day -- luck or command?
    ROUTINE = 3    # everything else with a record
    NO_DATA = 4    # never started, or not in their mission list at all


class DayPick(BaseModel):
    """A chosen day plus the reasoning, so the report can show its work."""

    day: int
    title: str
    priority: Priority
    pattern: SignalPattern
    reason: str = Field(description="Plain-language justification, for the audit trail")
    forced_coverage: bool = Field(
        False, description="True if the flagship-coverage rule overrode the ranking"
    )


class NextQuestion(BaseModel):
    """One generated interview turn, ready to send to the candidate."""

    day: int
    day_title: str
    mode: QuestionMode
    move: QuestionKind
    text: str
    rationale: str


# --------------------------------------------------------------------------
# Day ranking
# --------------------------------------------------------------------------


def _priority(mission: Mission | None) -> Priority:
    """Rank one day by how much a question about it would teach us.

    Tiers mirror `signals.classify()` but are not the same axis: classify says
    what happened, this says how much a question would teach us about it.
    """
    if mission is None or not mission.has_record:
        return Priority.NO_DATA
    if mission.skipped:
        return Priority.SKIPPED
    # Spec says attempts > 2. A mission they attempted and never passed is an
    # equally known gap regardless of the count, so it joins the same tier.
    if mission.attempts >= STRUGGLE_ATTEMPTS or not mission.passed:
        return Priority.STRUGGLED
    if mission.attempts <= 1 and mission.passed and mission.day in HIGH_VALUE_DAYS:
        return Priority.VERIFY
    return Priority.ROUTINE


def _reason(day: int, mission: Mission | None, priority: Priority) -> str:
    attempts = mission.attempts if mission else 0
    match priority:
        case Priority.SKIPPED:
            return (
                f"Skipped the day {day} mission outright -- worth hearing whether "
                "they can reason about it anyway."
            )
        case Priority.STRUGGLED if mission and not mission.passed:
            return (
                f"Attempted day {day} {attempts}x and never passed -- a known gap "
                "to locate precisely."
            )
        case Priority.STRUGGLED:
            return (
                f"Took {attempts} attempts to pass day {day} -- they got there, but "
                "the route suggests the understanding may be shallow."
            )
        case Priority.VERIFY:
            return (
                f"First-try pass on day {day}, a high-value topic -- verify it was "
                "command rather than luck."
            )
        case Priority.NO_DATA:
            return f"No cohort data for day {day} -- open ground."
        case _:
            return f"Passed day {day} in {attempts} attempts -- solid, not yet stretched."


def _sort_key(day: int, mission: Mission | None) -> tuple[int, int, int, int]:
    """Rank within a tier: more struggle first, then flagship days, then earliest.

    Fully deterministic, so the same candidate always gets the same interview
    plan and a reviewer can replay the decision.
    """
    return (
        int(_priority(mission)),
        -(mission.attempts if mission else 0),
        0 if day in HIGH_VALUE_DAYS else 1,
        day,
    )


# --------------------------------------------------------------------------
# Day selection
# --------------------------------------------------------------------------


def pick_next_day(
    candidate: Candidate,
    days_covered: list[int] | set[int],
    curriculum: Curriculum | None = None,
) -> DayPick | None:
    """Choose the next curriculum day to probe.

    Priority order:
      (a) skipped missions -- ask them to explain despite skipping
      (b) missions with more than two attempts (or attempted and never passed)
      (c) first-try passes on high-value days -- verify it wasn't luck
      (d) an override: if no day from 21-24 (Agentic AI/MCP) or 7-10
          (Embeddings/Vector) has been covered by the 4th pick, the pool is
          restricted to those bands until one is.

    Pass `curriculum` to let days the candidate has no mission row for become
    candidates too -- which the coverage rule needs when a candidate skipped
    every flagship day. Returns None when every available day is covered.
    """
    covered = set(days_covered)

    if curriculum is not None and len(curriculum) > 0:
        all_days = curriculum.day_numbers()
    else:
        all_days = sorted({m.day for m in candidate.missions})

    pool = [d for d in all_days if d not in covered]
    if not pool:
        return None

    # Rule (d): once we are one pick away from the deadline with no flagship day
    # covered, force the choice into those bands and keep it there until we land
    # one. Applied before ranking so it genuinely overrides the signal order.
    forced = False
    if len(covered) >= COVERAGE_DEADLINE - 1 and not (FLAGSHIP_DAYS & covered):
        flagship = [d for d in pool if d in FLAGSHIP_DAYS]
        if flagship:
            pool, forced = flagship, True

    day = min(pool, key=lambda d: _sort_key(d, candidate.mission_for(d)))
    mission = candidate.mission_for(day)
    priority = _priority(mission)

    reason = _reason(day, mission, priority)
    if forced:
        band = "Agentic AI/MCP" if day in AGENTIC_DAYS else "Embeddings/Vector"
        reason = f"{reason} Selected now to guarantee {band} coverage."

    curriculum_day = curriculum.get(day) if curriculum else None
    title = (
        curriculum_day.title
        if curriculum_day
        else (mission.title if mission and mission.title else f"Day {day}")
    )

    return DayPick(
        day=day,
        title=title,
        priority=priority,
        pattern=classify(mission),
        reason=reason,
        forced_coverage=forced,
    )


# --------------------------------------------------------------------------
# Answer depth
# --------------------------------------------------------------------------

# Phrases that make an answer a non-answer no matter how long it is. These are
# claims about *not knowing*, so nothing that follows rescues them.
#
# Deliberately excluded: "I skipped it", "we never used that". Those are claims
# about history, not knowledge -- and since tier (a) exists specifically to ask
# about skipped missions, "I skipped the lab, but here's how it works" is the
# most likely shape of a *good* answer in this interview. Length and specificity
# judge those on their merits instead.
NON_ANSWER_PHRASES = (
    "i don't know", "i dont know", "no idea", "not sure", "i forgot",
    "can't remember", "cant remember", "don't remember", "dont remember",
    "no clue",
)

SUBSTANTIAL_WORDS = 35  # long enough to carry its own weight
MINIMUM_WORDS = 12      # below this, nothing has been said yet
MIN_SPECIFICS = 2       # concrete anchors a middling-length answer needs
SHALLOW_SCORE = 0.5     # graded answers below this get probed

# Concrete anchors -- the tokens that separate "we used the vector thing" from
# "I raised ef_search until p95 stayed under 200ms".
_SPECIFIC_TOKEN = re.compile(
    r"`[^`]+`"                  # `code spans`
    r"|\b\w+\.\w+\b"          # dotted.paths, file.ext
    r"|\b\w+_\w+\b"            # snake_case identifiers
    r"|\b\w*[a-z][A-Z]\w*\b"   # camelCase, and LoRA / QLoRA / pgVector
    r"|\b[A-Z]{2,}\b"           # ALL-CAPS acronyms: HNSW, RAG, MCP, API
    r"|\b\w*\d+\w*\b"         # numbers and alphanumerics: 400, p95, 200ms, 4-bit
)


@lru_cache(maxsize=512)
def _term_pattern(term: str) -> re.Pattern[str]:
    """Match `term` as a whole word or whole phrase, never mid-word.

    Lookarounds rather than `\b` so terms that begin or end with punctuation
    ("python-docx", "qwen2.5-coder") still anchor correctly -- `\b` is defined
    relative to word characters and misbehaves at a punctuation edge.
    """
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")


def _matched_tools(lowered: str, terms: set[str]) -> set[str]:
    """Tool names the answer actually says, each credited once.

    Two rules, both of which a plain substring test gets wrong:

    * Whole words only. "decline" is not a mention of the Cline editor, and
      "digit" is not a mention of git.
    * A name contained in a longer matched name is the same mention, not a
      second one. Saying "Sentence Transformers" names one tool; it should not
      also score the bare "Transformers" entry. Note this is genuinely about
      containment, not length: LoRA and QLoRA both survive, because neither
      contains the other at a word boundary.
    """
    hits = {t for t in terms if t and _term_pattern(t).search(lowered)}
    return {
        t for t in hits
        if not any(other != t and _term_pattern(t).search(other) for other in hits)
    }


def specificity_anchors(
    answer: str,
    curriculum_day: CurriculumDay | None = None,
    vocabulary: frozenset[str] | None = None,
) -> dict[str, set[str]]:
    """The concrete references an answer contains, split by where they came from.

    `tools` are names drawn from the curriculum's own `tools[]` entries;
    `tokens` are pattern-matched anchors (identifiers, acronyms, numbers). The
    two sets are disjoint: a token that is merely a fragment of a tool name we
    already credited is the same mention seen twice, so it is dropped.

    Returned rather than counted so the scoring can be audited -- see
    `scripts/sanity_check.py`.

    `vocabulary` is the whole curriculum's tool list (see
    `Curriculum.tool_vocabulary`). Without it, plain lowercase tool names like
    "chromadb" are invisible to the regex and a specific answer reads as vague.
    """
    lowered = answer.lower()

    terms = set(vocabulary or ())
    if curriculum_day:
        terms |= {t.lower() for t in curriculum_day.tools}

    tools = _matched_tools(lowered, terms)
    tokens = {m.group(0).lower() for m in _SPECIFIC_TOKEN.finditer(answer)}
    tokens -= {
        tok for tok in tokens if any(_term_pattern(tok).search(t) for t in tools)
    }

    return {"tools": tools, "tokens": tokens}


def _specificity(
    answer: str,
    curriculum_day: CurriculumDay | None = None,
    vocabulary: frozenset[str] | None = None,
) -> int:
    """How many distinct concrete references an answer contains."""
    anchors = specificity_anchors(answer, curriculum_day, vocabulary)
    return len(anchors["tools"] | anchors["tokens"])


def is_non_answer(answer: str) -> bool:
    """They did not engage at all -- blank, or an explicit 'I don't know'."""
    text = (answer or "").strip().lower()
    return not text or any(p in text for p in NON_ANSWER_PHRASES)


def is_shallow(
    answer: str,
    *,
    curriculum_day: CurriculumDay | None = None,
    vocabulary: frozenset[str] | None = None,
) -> bool:
    """Heuristic depth check, used when no grader has evaluated the answer.

    Long answers pass, non-answers fail, and the middle is decided on whether
    they named anything concrete -- a specific tool, a number, an identifier.
    Errs permissive: a wrongly-probed candidate is a worse outcome than a
    wrongly-advanced one, and MAX_FOLLOW_UPS_PER_DAY bounds the damage anyway.
    """
    if is_non_answer(answer):
        return True

    words = len(answer.split())
    if words >= SUBSTANTIAL_WORDS:
        return False
    if words < MINIMUM_WORDS:
        return True
    return _specificity(answer, curriculum_day, vocabulary) < MIN_SPECIFICS


def needs_follow_up(
    turn: DayTurn,
    *,
    curriculum_day: CurriculumDay | None = None,
    vocabulary: frozenset[str] | None = None,
) -> bool:
    """Should we stay on this day rather than moving on?

    Prefers the grader's verdict when one exists; falls back to the heuristic so
    the engine works before the evaluation pipeline is wired up.
    """
    if turn.evaluation is not None:
        return (
            turn.evaluation.needs_follow_up
            or turn.evaluation.score < SHALLOW_SCORE
            or turn.evaluation.level
            in {UnderstandingLevel.NONE, UnderstandingLevel.RECALL}
        )
    return is_shallow(turn.answer, curriculum_day=curriculum_day, vocabulary=vocabulary)


def _follow_up_move(turn: DayTurn) -> QuestionKind:
    """A thin answer gets probed; a blank one gets diagnosed."""
    if turn.evaluation is not None:
        if turn.evaluation.level is UnderstandingLevel.NONE:
            return QuestionKind.DIAGNOSE_GAP
        return QuestionKind.PROBE_DEPTH
    return QuestionKind.DIAGNOSE_GAP if is_non_answer(turn.answer) else QuestionKind.PROBE_DEPTH


# --------------------------------------------------------------------------
# Question generation
# --------------------------------------------------------------------------


class QuestionTarget(BaseModel):
    """Internal: what the next turn should be about, before the LLM sees it."""

    day: int
    title: str
    mode: QuestionMode
    move: QuestionKind
    reason: str


def resolve_target(
    session: InterviewSession, curriculum: Curriculum | None = None
) -> QuestionTarget | None:
    """Decide the day and the interview move for the next turn.

    Split out from `next_question` so the selection logic can be tested without
    an LLM. Returns None when the interview is over.
    """
    if session.asked_count >= session.max_questions:
        return None

    last = session.last_turn
    if last is not None and last.answer:
        day_ctx = curriculum.get(last.day) if curriculum else None
        vocab = curriculum.tool_vocabulary() if curriculum else None
        if (
            needs_follow_up(last, curriculum_day=day_ctx, vocabulary=vocab)
            and session.follow_ups_on(last.day) < MAX_FOLLOW_UPS_PER_DAY
        ):
            title = day_ctx.title if day_ctx else f"Day {last.day}"
            return QuestionTarget(
                day=last.day,
                title=title,
                mode=QuestionMode.FOLLOW_UP,
                move=_follow_up_move(last),
                reason=f"Last answer on day {last.day} was too thin to score.",
            )

    pick = pick_next_day(session.candidate, session.days_covered, curriculum)
    if pick is None:
        return None

    return QuestionTarget(
        day=pick.day,
        title=pick.title,
        mode=QuestionMode.OPENING,
        move=opening_move(pick.pattern),
        reason=pick.reason,
    )


def next_question(
    session: InterviewSession,
    curriculum: Curriculum | None = None,
    *,
    client: llm.SupportsMessages | None = None,
    model: str = llm.DEFAULT_MODEL,
    max_tokens: int = llm.DEFAULT_MAX_TOKENS,
    effort: str = llm.DEFAULT_EFFORT,
    history_turns: int = prompts.DEFAULT_HISTORY_TURNS,
) -> NextQuestion | None:
    """Generate the next interview turn.

    Injects the current day's objectives, the candidate's record on that day, and
    the last few exchanges, then asks the LLM for exactly one question. If the
    previous answer was shallow it probes deeper on the *same* day instead of
    moving on.

    Does not mutate `session` -- the caller appends the resulting `DayTurn` once
    the candidate has answered. Returns None when the interview is complete.
    """
    target = resolve_target(session, curriculum)
    if target is None:
        return None

    payload = prompts.build_message_payload(
        session,
        curriculum_day=curriculum.get(target.day) if curriculum else None,
        mission=session.candidate.mission_for(target.day),
        mode=target.mode,
        move=target.move,
        reason=target.reason,
        history_turns=history_turns,
    )

    text = llm.complete(
        payload, client=client, model=model, max_tokens=max_tokens, effort=effort
    )

    return NextQuestion(
        day=target.day,
        day_title=target.title,
        mode=target.mode,
        move=target.move,
        text=text,
        rationale=target.reason,
    )
