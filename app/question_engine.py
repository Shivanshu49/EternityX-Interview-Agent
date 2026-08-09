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
from typing import Any
from functools import lru_cache

from pydantic import BaseModel, Field

from app import evaluation, llm, prompts
from app.models import (
    AnswerEvaluation,
    Candidate,
    coerce_curriculum,
    coerce_session,
    Curriculum,
    CurriculumDay,
    DayProfile,
    DayTurn,
    InterviewSession,
    Mission,
    QuestionKind,
    QuestionMode,
    SignalPattern,
    UnderstandingLevel,
)
from app.signals import (
    GRINDER_ATTEMPT_THRESHOLD,
    build_profiles,
    classify,
    fold_beliefs,
    opening_move,
    unknown_profile,
)

# --------------------------------------------------------------------------
# Curriculum bands
# --------------------------------------------------------------------------
#
# One band per topic the challenge brief names: RAG, vector databases, prompt
# engineering, agentic AI, MCP, deployment, and production systems. The
# interview guarantees at least one band is covered even when the candidate's
# record points elsewhere, and prefers a band it has not touched when ranking
# ties, so a single interview walks across topics instead of pooling in one.
#
# They double as the "high-value" set: a first-try pass here is worth verifying,
# because these are the days where luck and understanding look most alike from
# the outside.
#
# Bands are topics, not modules. Module 4 spans days 11-15 but mixes RAG,
# prompting and fine-tuning, so it is split; module 7 spans 25-28 but only its
# back half is about shipping.

VECTOR_DAYS = frozenset(range(7, 11))      # Embeddings / vector search
RAG_DAYS = frozenset({11})                 # RAG end-to-end & LLM API basics
PROMPTING_DAYS = frozenset({12, 13})       # Prompt engineering, incl. tool schemas
AGENTIC_DAYS = frozenset(range(21, 25))    # Agentic AI / MCP
DEPLOYMENT_DAYS = frozenset({27, 28})      # Guardrails, Docker/Kubernetes
PRODUCTION_DAYS = frozenset(range(29, 32))  # Observability, readiness, capstone

FLAGSHIP_DAYS = (
    VECTOR_DAYS | RAG_DAYS | PROMPTING_DAYS
    | AGENTIC_DAYS | DEPLOYMENT_DAYS | PRODUCTION_DAYS
)
HIGH_VALUE_DAYS = FLAGSHIP_DAYS

# Human-readable band names, for the audit trail on a forced pick. Ordered as
# the cohort teaches them, so the rotation walks the syllabus forwards.
_BAND_NAMES = (
    (VECTOR_DAYS, "Embeddings/Vector"),
    (RAG_DAYS, "RAG"),
    (PROMPTING_DAYS, "Prompt Engineering"),
    (AGENTIC_DAYS, "Agentic AI/MCP"),
    (DEPLOYMENT_DAYS, "Deployment"),
    (PRODUCTION_DAYS, "Production"),
)

# By the Nth distinct day we probe, one flagship day must be among them.
COVERAGE_DEADLINE = 4

# "attempts > 2" from the spec. Same number that makes a pass a GRINDER, so it
# is imported rather than restated.
STRUGGLE_ATTEMPTS = GRINDER_ATTEMPT_THRESHOLD

# Cap on consecutive probes into one day, so a candidate who keeps giving thin
# answers does not burn the whole interview on a single topic.
MAX_FOLLOW_UPS_PER_DAY = 2

# Distinct days an interview owes before depth is worth more than breadth. This
# mirrors the API layer's completion gate (`session_store.can_finish`), and the
# two must not drift: probing is the only thing that competes with coverage, so
# an engine that ratholes freely can stop the interview ever qualifying to end.
MIN_DISTINCT_DAYS = 4


def follow_up_budget(days_covered: int) -> int:
    """How many probes one day may take, given how much breadth is still owed.

    While the interview is short of `MIN_DISTINCT_DAYS`, a day gets one probe
    rather than two. That still lets a thin answer be challenged, but bounds the
    worst case -- a candidate answering everything in three words -- at two
    questions per day, so the coverage minimum is always reachable inside the
    question budget instead of being crowded out by depth.
    """
    return 1 if days_covered < MIN_DISTINCT_DAYS else MAX_FOLLOW_UPS_PER_DAY


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
    """One generated interview turn, ready to send to the candidate.

    Field names deliberately match `models.QuestionResult` so the API layer can
    do `QuestionResult.model_validate(result)` straight off this object -- that
    model sets `from_attributes=True`. `day_title`, `mode`, and `move` are extra
    detail for the report and are simply not read by the HTTP contract.
    """

    day: int
    reply: str = Field(description="The question text, as the candidate will see it")
    tier: str = Field(description="Selection tier, e.g. 'SKIPPED' -- Priority.name")
    pattern: str = Field(description="SignalPattern value for the day, e.g. 'avoided'")
    reason: str = Field(description="Why this day, for the audit trail")
    is_follow_up: bool

    day_title: str = ""
    mode: QuestionMode = QuestionMode.OPENING
    move: QuestionKind = QuestionKind.APPLY

    last_evaluation: AnswerEvaluation | None = Field(
        None,
        description=(
            "Grade produced for the answer that preceded this question, when "
            "the adaptive-eval path ran. Returned so the API layer can persist "
            "it onto the candidate's history entry; None whenever no fresh "
            "grade exists."
        ),
    )


# --------------------------------------------------------------------------
# Day ranking
# --------------------------------------------------------------------------


def priority_for(mission: Mission | None) -> Priority:
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


def _bands_without_coverage(covered: set[int]) -> frozenset[int]:
    """Days belonging to a flagship band the interview has not touched yet."""
    return frozenset().union(
        *(days for days, _ in _BAND_NAMES if not (days & covered))
    )


def _sort_key(
    day: int, mission: Mission | None, unseen_bands: frozenset[int] = frozenset()
) -> tuple[int, int, int, int]:
    """Rank within a tier: more struggle first, then flagship days, then earliest.

    The flagship component prefers a band nothing has been asked about yet.
    Without that, ties break on day number and the interview clusters in the
    earliest band -- every candidate got Embeddings/Vector and half never
    reached Agentic AI/MCP, because day 7 outranks day 21 on number alone.

    Fully deterministic, so the same candidate always gets the same interview
    plan and a reviewer can replay the decision.
    """
    if day in unseen_bands:
        flagship_rank = 0
    elif day in HIGH_VALUE_DAYS:
        flagship_rank = 1
    else:
        flagship_rank = 2
    return (
        int(priority_for(mission)),
        -(mission.attempts if mission else 0),
        flagship_rank,
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
      (d) an override: if no day from 21-24 (Agentic AI/MCP), 11 (RAG) or 7-10
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

    unseen_bands = _bands_without_coverage(covered)
    day = min(pool, key=lambda d: _sort_key(d, candidate.mission_for(d), unseen_bands))
    mission = candidate.mission_for(day)
    priority = priority_for(mission)

    reason = _reason(day, mission, priority)
    if forced:
        band = next((name for days, name in _BAND_NAMES if day in days), "flagship")
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
# Belief-driven probing (adaptive-eval path only)
# --------------------------------------------------------------------------

# A probe fires when both hold after folding the new grade in. Mastery below
# 0.5 means the belief still says "as likely confused as not"; uncertainty at
# or above 0.25 means one direct observation has not resolved a doubtful prior.
# With update_belief's weight of 0.6, one answer leaves uncertainty >= 0.25
# only for the GRINDER (0.75 -> 0.30), AVOIDED (0.80 -> 0.32) and UNKNOWN
# (0.90 -> 0.36) priors -- exactly the patterns whose records say the least. A
# STRUGGLED prior (0.45 -> 0.18) does not qualify: a failed mission plus a
# graded answer is already two real observations.
BELIEF_PROBE_MASTERY = 0.5
BELIEF_PROBE_UNCERTAINTY = 0.25


def belief_wants_probe(profile: DayProfile) -> bool:
    """Stay on this day even though the answer itself passed?

    Catches the case the per-answer check cannot: a middling-but-passing answer
    (score just over the shallow line) on a day whose record was uninformative.
    One such answer should not clear a day the candidate ground through or
    skipped; a second, harder question settles it.
    """
    return (
        profile.mastery < BELIEF_PROBE_MASTERY
        and profile.uncertainty >= BELIEF_PROBE_UNCERTAINTY
    )


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
    tier: str
    pattern: str
    answer_was_thin: bool = Field(
        True,
        description=(
            "Follow-ups only. False when the answer itself passed and the probe "
            "is belief-driven, so the directive must not call it thin."
        ),
    )


def resolve_target(
    session: InterviewSession | dict[str, Any],
    curriculum: Curriculum | dict[str, Any] | None = None,
    *,
    last_evaluation: AnswerEvaluation | None = None,
) -> QuestionTarget | None:
    """Decide the day and the interview move for the next turn.

    Split out from `next_question` so the selection logic can be tested without
    an LLM. Returns None when the interview is over.

    `last_evaluation` is a grade for the newest answer, produced by the caller.
    When present (or already on the turn), the follow-up decision runs on the
    grade and on the folded belief for that day instead of on the word-count
    heuristic. When absent, behaviour is exactly the pre-grading engine.
    """
    session = coerce_session(session)
    curriculum = coerce_curriculum(curriculum)

    if session.asked_count >= session.max_questions:
        return None

    last = session.last_turn
    if last is not None and last.answer:
        if last_evaluation is not None and last.evaluation is None:
            # A copy, never the caller's turn: this function mutates nothing.
            last = last.model_copy(update={"evaluation": last_evaluation})

        day_ctx = curriculum.get(last.day) if curriculum else None
        vocab = curriculum.tool_vocabulary() if curriculum else None
        budget = follow_up_budget(len(session.days_covered))

        follow = needs_follow_up(last, curriculum_day=day_ctx, vocabulary=vocab)
        reason = f"Last answer on day {last.day} was too thin to score."
        answer_was_thin = True

        if last.evaluation is not None:
            grade = last.evaluation
            beliefs = fold_beliefs(
                build_profiles(session.candidate, curriculum),
                [*session.turns[:-1], last],
            )
            posterior = beliefs.get(last.day) or unknown_profile(last.day)

            if follow:
                reason = (
                    f"Answer on day {last.day} graded {grade.score:.2f} at "
                    f"{grade.level.value} level -- not settled yet."
                )
            elif belief_wants_probe(posterior):
                # The answer passed on its own terms, but one passing answer on
                # a day whose record was uninformative leaves the belief where
                # a coin flip would. Spend one more question here.
                follow = True
                answer_was_thin = False
                reason = (
                    f"Answer held up ({grade.score:.2f}, {grade.level.value}), "
                    f"but day {last.day}'s belief is unresolved: mastery "
                    f"{posterior.mastery:.2f}, uncertainty "
                    f"{posterior.uncertainty:.2f} after grading."
                )

        if follow and session.follow_ups_on(last.day) < budget:
            title = day_ctx.title if day_ctx else f"Day {last.day}"
            mission = session.candidate.mission_for(last.day)
            return QuestionTarget(
                day=last.day,
                title=title,
                mode=QuestionMode.FOLLOW_UP,
                move=_follow_up_move(last),
                reason=reason,
                tier=priority_for(mission).name,
                pattern=classify(mission).value,
                answer_was_thin=answer_was_thin,
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
        tier=pick.priority.name,
        pattern=pick.pattern.value,
    )


def next_question(
    session: InterviewSession | dict[str, Any],
    curriculum: Curriculum | dict[str, Any] | None = None,
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

    With ENABLE_ADAPTIVE_EVAL on, the newest answer is graded first and the
    grade drives the follow-up decision; the grade rides back on
    `last_evaluation` so the caller can persist it. A grading failure returns
    None from the grader and the heuristic decides instead -- the interview
    never stalls on the evaluator.
    """
    session = coerce_session(session)
    curriculum = coerce_curriculum(curriculum)

    last = session.last_turn
    last_eval: AnswerEvaluation | None = None
    if (
        evaluation.ENABLE_ADAPTIVE_EVAL
        and last is not None
        and last.answer
        and last.evaluation is None
    ):
        last_eval = evaluation.evaluate_answer(
            last,
            curriculum_day=curriculum.get(last.day) if curriculum else None,
            mission=session.candidate.mission_for(last.day),
            client=client,
            model=model,
        )

    target = resolve_target(session, curriculum, last_evaluation=last_eval)
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
        answer_was_thin=target.answer_was_thin,
    )

    text = llm.complete(
        payload, client=client, model=model, max_tokens=max_tokens, effort=effort
    )

    return NextQuestion(
        day=target.day,
        reply=text,
        tier=target.tier,
        pattern=target.pattern,
        reason=target.reason,
        is_follow_up=target.mode is QuestionMode.FOLLOW_UP,
        day_title=target.title,
        mode=target.mode,
        move=target.move,
        last_evaluation=last_eval,
    )


def fallback_question(
    session: InterviewSession | dict[str, Any],
    curriculum: Curriculum | dict[str, Any] | None = None,
) -> NextQuestion | None:
    """Build one contextual question without a model call.

    This is an availability path, not a second selection engine: it reuses the
    same candidate ranking, coverage policy, and follow-up decision as
    ``next_question``. Only the final wording is deterministic. That keeps a
    transient provider/WAF outage from breaking a live interview while
    preserving the 8-question, 4-day behavior and selection audit trail.
    """
    typed_session = coerce_session(session)
    typed_curriculum = coerce_curriculum(curriculum)
    target = resolve_target(typed_session, typed_curriculum)
    if target is None:
        return None

    if target.mode is QuestionMode.FOLLOW_UP and typed_session.last_turn:
        answer = " ".join(typed_session.last_turn.answer.split())
        excerpt = answer[:120].rstrip()
        if len(answer) > 120:
            excerpt += "..."
        reply = (
            f'You said, "{excerpt}". Which concrete failure mode would you '
            "test first to validate that approach?"
            if excerpt
            else "Which concrete example would make your reasoning testable?"
        )
    else:
        day = typed_curriculum.get(target.day) if typed_curriculum else None
        tool = day.tools[0] if day and day.tools else None
        tool_phrase = f" using {tool}" if tool else ""
        reply = (
            f"What design would you choose for a production {target.title} "
            f"system{tool_phrase}, including the tradeoff that would drive "
            "your choice?"
        )

    return NextQuestion(
        day=target.day,
        reply=reply,
        tier=target.tier,
        pattern=target.pattern,
        reason=f"{target.reason} Selection retained during provider fallback.",
        is_follow_up=target.mode is QuestionMode.FOLLOW_UP,
        day_title=target.title,
        mode=target.mode,
        move=target.move,
    )
