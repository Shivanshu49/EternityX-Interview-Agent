"""Prompt templates for the interview agent. Owner: A.

Two jobs:

1. `SYSTEM_PROMPT` -- the interviewer's persona and hard constraints.
2. `build_message_payload` -- turns session state plus one day's curriculum
   context into the exact `{"system": ..., "messages": [...]}` dict that
   `llm.complete` passes to `client.messages.create`.

Every function here is pure: strings in, strings out, no session mutation.
"""

from __future__ import annotations

from typing import Any, TypedDict

from app.models import (
    Candidate,
    CurriculumDay,
    DayTurn,
    InterviewSession,
    Mission,
    QuestionKind,
    QuestionMode,
)

# How many prior exchanges to replay. Enough for the model to hear the thread of
# the conversation; short enough that the prompt stays cheap and focused.
DEFAULT_HISTORY_TURNS = 3


class MessagePayload(TypedDict):
    """Exactly the kwargs `client.messages.create` needs beyond model/max_tokens."""

    system: str
    messages: list[dict[str, Any]]


# --------------------------------------------------------------------------
# Persona
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior engineer conducting a live technical interview. The candidate \
just finished a 31-day AI engineering cohort, and you have their actual \
performance data in front of you: which missions they passed first try, which \
ones took several attempts, and which ones they skipped entirely.

You are having a conversation, not administering a quiz. React to what the \
candidate actually says. If an answer is sharp, say so briefly and push into \
harder ground. If it is vague, ask them to make it concrete -- a specific bug, \
a specific tradeoff, a specific thing they built. If they are clearly out of \
their depth, ease off and find the edge of what they do know rather than \
letting them flounder.

Ask exactly one question per reply. Not two questions joined by "and", not a \
numbered list, not a main question with sub-parts. One. The candidate has to be \
able to answer it in a single breath, and a reply containing more than one \
question makes them pick which to answer and skip the rest.

Do not lecture. You are not here to explain embeddings to them or to correct \
their answer at length -- if they get something wrong, that is information for \
you, and at most a sentence back to them before your next question. Never give \
away the answer inside the question.

Write the way a person talks. A short reaction to what they just said, then the \
question. No preamble like "Great question" or "Let me ask you about", no \
headers, no bullet points, no markdown. Two or three sentences total.

You know how they performed, but never recite the data at them. Do not say \
"I see you skipped day 12" or "your records show three attempts". Let the \
performance data shape which question you ask, not what you say out loud.

Output only what you would say to the candidate."""


# What move to make, given how the candidate behaved on this day. Keyed by the
# QuestionKind that `signals.opening_move()` derives from the signal pattern.
MOVE_GUIDANCE: dict[QuestionKind, str] = {
    QuestionKind.PROBE_DEPTH: (
        "They got there eventually, but the route suggests the understanding may be "
        "shallow. Ask about *why* their approach works, not what it does."
    ),
    QuestionKind.EDGE_CASE: (
        "They have genuine command here. Skip the basics entirely and take them to "
        "the boundary -- where the standard approach breaks down."
    ),
    QuestionKind.SCAFFOLD: (
        "They avoided this topic, so assume low confidence rather than low ability. "
        "Open somewhere accessible and concrete that gives them a way in."
    ),
    QuestionKind.DIAGNOSE_GAP: (
        "They tried this and never got it working. Find where the understanding "
        "actually breaks -- ask what they expected to happen versus what did."
    ),
    QuestionKind.APPLY: (
        "Solid but unremarkable. Move it sideways: give them a situation they have "
        "not seen and ask how they would handle it."
    ),
    QuestionKind.CLARIFY: (
        "Their last answer was ambiguous. Resolve the ambiguity with one pointed "
        "question."
    ),
}


# --------------------------------------------------------------------------
# Context renderers
# --------------------------------------------------------------------------


def render_candidate_profile(candidate: Candidate) -> str:
    """The stable, once-per-interview framing: who this is and how they worked."""
    s = candidate.signals
    total = len(candidate.missions)
    skipped = sum(1 for m in candidate.missions if m.skipped)
    ground = sum(1 for m in candidate.missions if m.attempts > 2)

    return (
        f"You are interviewing {candidate.display_name}, who just completed a "
        f"31-day AI engineering cohort.\n\n"
        f"How they worked, across {total} missions:\n"
        f"- Committed code on {s.commit_days} of 31 days\n"
        f"- Completed {s.missions_completed} missions, "
        f"{s.missions_first_try} of them on the first try\n"
        f"- Skipped {skipped} missions outright\n"
        f"- Needed more than two attempts on {ground} missions\n\n"
        "This is background for you only. Never quote these numbers to the "
        "candidate.\n\n"
        "I will give you one day of the curriculum at a time, along with how they "
        "did on it. Ask your question and wait."
    )


def render_day_brief(
    curriculum_day: CurriculumDay | None, mission: Mission | None
) -> str:
    """The day's objectives and the candidate's record on it."""
    lines: list[str] = []

    if curriculum_day is not None:
        lines.append(f"Day {curriculum_day.day}: {curriculum_day.title}")
        if curriculum_day.type:
            lines.append(f"Format: {curriculum_day.type}")
        if curriculum_day.tools:
            lines.append(f"Tools used: {', '.join(curriculum_day.tools)}")
        if curriculum_day.objectives:
            lines.append("They were supposed to be able to:")
            lines.extend(f"  - {obj}" for obj in curriculum_day.objectives)
    elif mission is not None:
        lines.append(f"Day {mission.day}: {mission.title or 'untitled mission'}")

    lines.append("")
    lines.append(_render_performance(mission))
    return "\n".join(lines)


def _render_performance(mission: Mission | None) -> str:
    if mission is None:
        return "Their record: no data for this day."
    if mission.skipped:
        return "Their record: skipped this mission without attempting it."
    if mission.attempts == 0 and not mission.passed:
        return "Their record: never started this mission."
    outcome = "passed" if mission.passed else "never passed"
    plural = "" if mission.attempts == 1 else "s"
    return f"Their record: {outcome} it after {mission.attempts} attempt{plural}."


def render_history(turns: list[DayTurn], limit: int) -> list[dict[str, Any]]:
    """Replay the last `limit` exchanges as real conversation turns.

    Your questions become assistant turns and their answers become user turns,
    so the model hears the interview as a dialogue it was part of rather than as
    a transcript pasted into a prompt.
    """
    messages: list[dict[str, Any]] = []
    for turn in turns[-limit:] if limit > 0 else []:
        messages.append({"role": "assistant", "content": turn.question})
        messages.append(
            {"role": "user", "content": turn.answer.strip() or "(no answer given)"}
        )
    return messages


def render_directive(mode: QuestionMode, move: QuestionKind, reason: str) -> str:
    """The instruction for this specific turn -- what to ask and why."""
    guidance = MOVE_GUIDANCE.get(move, MOVE_GUIDANCE[QuestionKind.APPLY])

    if mode is QuestionMode.FOLLOW_UP:
        return (
            "That answer was thin -- not enough to tell whether they actually "
            "understand this or are repeating a definition. Stay on this same day. "
            "Do not move on.\n\n"
            f"{guidance}\n\n"
            "Ask one follow-up that makes them be specific. Reference something "
            "they just said so it lands as a real follow-up rather than a "
            "restatement. One question."
        )

    return (
        f"Why this day: {reason}\n\n"
        f"{guidance}\n\n"
        "Ask your opening question on this day. If the candidate has already "
        "answered something earlier in the interview, acknowledge the shift in a "
        "few words before you ask. One question."
    )


# --------------------------------------------------------------------------
# Payload assembly
# --------------------------------------------------------------------------


def build_message_payload(
    session: InterviewSession,
    *,
    curriculum_day: CurriculumDay | None,
    mission: Mission | None,
    mode: QuestionMode,
    move: QuestionKind,
    reason: str,
    history_turns: int = DEFAULT_HISTORY_TURNS,
) -> MessagePayload:
    """Build the full `client.messages.create` payload for the next question.

    Message order is deliberate and runs stable -> volatile: the candidate
    profile never changes within an interview, the replayed history grows
    slowly, and the per-turn brief goes last. That is also what the API's prefix
    caching wants.

    The first message must be a user turn, which is why the profile leads even
    when there is history to replay.
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": render_candidate_profile(session.candidate)}
    ]
    messages.extend(render_history(session.turns, history_turns))

    brief = render_day_brief(curriculum_day, mission)
    # The candidate's last answer is already in the replayed history above, so
    # the directive only has to say what to do about it.
    directive = render_directive(mode, move, reason)
    messages.append({"role": "user", "content": f"{brief}\n\n---\n\n{directive}"})

    return {"system": SYSTEM_PROMPT, "messages": messages}
