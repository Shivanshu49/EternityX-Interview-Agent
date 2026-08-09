"""Post-interview feedback. Owner: A.

Turns a finished session into the four-part `Feedback` the API returns, in one
LLM call.

The whole point is that the assessment is graded on the same axis the interview
was *built* on. There is no second scoring system here: every day in the
evidence block is labelled with the exact `Priority` tier that made the engine
choose it and the exact `SignalPattern` that `signals.classify()` derived from
the cohort record. Feedback is then the comparison of two things we already
have -- what the cohort data predicted, and what the candidate actually said.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app import llm, question_engine as qe
from app.models import (
    Candidate,
    Curriculum,
    CurriculumDay,
    DayTurn,
    Feedback,
    InterviewSession,
    Mission,
    coerce_curriculum,
    coerce_session,
)
from app.signals import classify, evidence


class DayEvidence(BaseModel):
    """Everything known about one day, prior belief and interview both."""

    day: int
    title: str
    tier: str = Field(description="Priority tier that made the engine pick this day")
    pattern: str = Field(description="SignalPattern derived from the cohort record")
    cohort_evidence: str = Field(description="What the mission record showed")
    objectives: list[str] = Field(default_factory=list)
    exchanges: list[tuple[str, str]] = Field(
        default_factory=list, description="(question, answer) pairs, in order"
    )
    probes: int = Field(0, description="Follow-ups spent, i.e. thin answers challenged")
    thin_answers: int = 0


# --------------------------------------------------------------------------
# Evidence assembly -- deterministic, no LLM
# --------------------------------------------------------------------------


def build_evidence(
    session: InterviewSession, curriculum: Curriculum | None = None
) -> list[DayEvidence]:
    """One entry per day the interview actually covered, in the order asked."""
    vocabulary = curriculum.tool_vocabulary() if curriculum else None
    by_day: dict[int, DayEvidence] = {}

    for turn in session.turns:
        mission = session.candidate.mission_for(turn.day)
        day_ctx: CurriculumDay | None = curriculum.get(turn.day) if curriculum else None

        entry = by_day.get(turn.day)
        if entry is None:
            pattern = classify(mission)
            entry = DayEvidence(
                day=turn.day,
                title=day_ctx.title if day_ctx else (mission.title if mission else f"Day {turn.day}"),
                tier=qe.priority_for(mission).name,
                pattern=pattern.value,
                cohort_evidence=evidence(pattern, mission),
                objectives=list(day_ctx.objectives) if day_ctx else [],
            )
            by_day[turn.day] = entry

        entry.exchanges.append((turn.question, turn.answer))
        if turn.follow_up:
            entry.probes += 1
        if turn.answer and qe.is_shallow(
            turn.answer, curriculum_day=day_ctx, vocabulary=vocabulary
        ):
            entry.thin_answers += 1

    return list(by_day.values())


def render_evidence(entry: DayEvidence) -> str:
    """One day's block of the prompt."""
    lines = [
        f"Day {entry.day}: {entry.title}",
        f"  Why the interview chose this day: tier {entry.tier}, "
        f"cohort pattern '{entry.pattern}'",
        f"  Cohort record: {entry.cohort_evidence}",
    ]
    if entry.objectives:
        lines.append("  They were supposed to be able to:")
        lines.extend(f"    - {o}" for o in entry.objectives)
    if entry.probes:
        lines.append(
            f"  The interviewer had to probe {entry.probes}x here after thin answers."
        )
    lines.append("  Transcript:")
    for question, answer in entry.exchanges:
        lines.append(f"    Q: {question}")
        lines.append(f"    A: {answer or '(no answer given)'}")
    return "\n".join(lines)


def render_candidate_summary(candidate: Candidate) -> str:
    s = candidate.signals
    missions = candidate.missions
    return (
        f"Candidate: {candidate.display_name}\n"
        f"Cohort totals: committed on {s.commit_days}/31 days, "
        f"{s.missions_completed} missions completed, "
        f"{s.missions_first_try} passed first try, "
        f"{sum(1 for m in missions if m.skipped)} skipped, "
        f"{sum(1 for m in missions if m.attempts > 2)} needing more than two attempts."
    )


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

FEEDBACK_SYSTEM_PROMPT = """\
You are a senior engineer writing up a technical interview you just conducted. \
The candidate completed a 31-day AI engineering cohort, and you have both their \
cohort record and the interview transcript.

Every day in the evidence was chosen for a reason, and that reason is given to \
you as a tier and a cohort pattern. Ground your assessment in those. The tiers \
mean:

- SKIPPED / pattern 'avoided': they never attempted this mission. The interview \
asked anyway. What matters is whether they could reason about it regardless -- \
a skipped mission they can still explain is a scheduling gap, not a knowledge \
gap, and those are very different findings.
- STRUGGLED / pattern 'grinder': they passed only after many attempts. The \
question is whether the understanding caught up with the passing grade.
- STRUGGLED / pattern 'struggled': they attempted it and never passed. Expect a \
real gap; the interview was trying to locate exactly where it starts.
- VERIFY / pattern 'fluent': a first-try pass on a high-value topic. The \
interview was testing whether that was command or luck.
- ROUTINE / pattern 'steady': passed in a couple of attempts, no signal either \
way going in.

Judge each day on what the candidate said against what the record predicted. \
The interesting findings are the mismatches in both directions: a topic they \
skipped but clearly understand, and a topic they passed but cannot explain. Say \
which it is. Where the interviewer had to probe after a thin answer, note \
whether the probe recovered anything.

Be specific and concrete. Cite what they actually said -- the tool they named, \
the tradeoff they described, the thing they got wrong. Never cite attempt \
counts or tier names back to the candidate; those are your evidence, not your \
vocabulary. Avoid vague praise: "strong on retrieval" is useless, "explained \
why chunk overlap fixes a straddled answer" is not.

Do not invent a numeric score, grade, or rating. Do not comment on any day that \
is not in the evidence below. If the interview was too short to judge \
something, say so plainly rather than filling space.

Write for a hiring manager who did not watch the interview. Punctuate plainly: \
commas, full stops, question marks. Never use an em dash or an en dash; use a \
comma or a new sentence instead.

Fields:
- summary: two or three sentences. What kind of engineer is this, and what did \
the interview establish that the cohort record alone did not?
- strengths: things the interview positively demonstrated. Each one tied to \
something they said.
- gaps: what is genuinely missing or shaky, and how you know.
- next: concrete things to work on, ordered by what would move them furthest."""


def build_feedback_payload(
    session: InterviewSession, curriculum: Curriculum | None = None
) -> dict[str, Any]:
    """Assemble the single message payload for the feedback call."""
    entries = build_evidence(session, curriculum)

    body = [render_candidate_summary(session.candidate), ""]
    if entries:
        body.append(
            f"The interview covered {len(entries)} curriculum "
            f"{'day' if len(entries) == 1 else 'days'} across "
            f"{session.asked_count} questions.\n"
        )
        body.extend(render_evidence(e) + "\n" for e in entries)
    else:
        body.append("No questions were asked. Say so; do not speculate.\n")

    body.append("Write the assessment.")

    return {
        "system": FEEDBACK_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": "\n".join(body)}],
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def generate_feedback(
    session: InterviewSession | dict[str, Any],
    curriculum: Curriculum | dict[str, Any] | None = None,
    *,
    client: llm.SupportsMessages | None = None,
    model: str = llm.DEFAULT_MODEL,
    max_tokens: int = llm.FEEDBACK_MAX_TOKENS,
    effort: str = llm.FEEDBACK_EFFORT,
) -> Feedback:
    """Generate the four-part assessment for a finished interview.

    Accepts the API layer's dict session or the typed model, same as
    `question_engine.next_question`. Pure: nothing is mutated.
    """
    session = coerce_session(session)
    curriculum = coerce_curriculum(curriculum)

    raw = llm.complete_json(
        build_feedback_payload(session, curriculum),
        Feedback.model_json_schema(),
        client=client,
        model=model,
        max_tokens=max_tokens,
        effort=effort,
    )
    try:
        return Feedback.model_validate(raw)
    except ValidationError as exc:
        raise llm.LLMError(f"Feedback did not match the required shape: {exc}") from exc
