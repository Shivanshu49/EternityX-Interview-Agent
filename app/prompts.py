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

# How many prior exchanges to replay. An interview runs 8 questions plus at most
# two probes per day, so this covers the whole thing: the model can make callbacks
# to the opening answers and notice when a late answer contradicts an early one.
# At 3 -- the previous value -- question 8 could not see answers 1 through 4, and
# the interviewer's own instruction to acknowledge a shift in position was
# unfollowable for anything outside the window. The answers are a few sentences
# each, so replaying all of them is cheap, and the payload is ordered
# stable-to-volatile (see `build_message_payload`) so the prefix still caches.
DEFAULT_HISTORY_TURNS = 16


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

Punctuate plainly: commas, full stops, question marks. Never use an em dash or \
an en dash. Where you would reach for one, use a comma or start a new sentence.

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


# Words that mark a role as a building role. Tested by presence rather than by
# listing the non-technical titles, so a job title nobody anticipated defaults to
# the plainer register instead of assuming vocabulary the candidate may not have.
_ENGINEERING_HINTS = (
    "engineer", "developer", "architect", "programmer", "scientist", "devops",
    "sre", "computer science", "software", "data", "technical", "it ",
)

# Titles that outrank a year count. A principal with 8 years is not a mid.
_SENIOR_TITLES = ("principal", "distinguished", "staff", "lead", "head", "director", "vp")
_JUNIOR_TITLES = ("intern", "junior", "trainee", "graduate", "apprentice")

SENIOR_YEARS = 12
EARLY_YEARS = 2


def render_calibration(candidate: Candidate) -> str:
    """How hard to push, and in what vocabulary.

    Seniority changes what a good question is, not how demanding it is. A
    distinguished engineer should not be asked what an embedding is, and an
    intern should not be asked to defend a rollout strategy they have never
    owned. Neither should be talked down to.
    """
    role = (candidate.member.job_role or "").lower()
    years = candidate.member.years_experience

    senior = any(t in role for t in _SENIOR_TITLES) or (
        years is not None and years >= SENIOR_YEARS
    )
    early = any(t in role for t in _JUNIOR_TITLES) or (
        years is not None and years <= EARLY_YEARS
    )
    builds = any(h in role for h in _ENGINEERING_HINTS)

    if senior and not early:
        depth = (
            "This is a senior engineer. Skip definitions entirely; they will find "
            "them insulting. Ask about tradeoffs, failure modes, and what they "
            "would do differently at scale. It is fair to disagree with them and "
            "see how they defend a position."
        )
    elif early:
        depth = (
            "This is an early-career candidate. Fundamentals are fair game and "
            "worth confirming, but ask them about what they actually built rather "
            "than about production experience they have not had yet. Do not "
            "soften the question, just aim it at ground they have stood on."
        )
    else:
        depth = (
            "This is a mid-level engineer. Assume the vocabulary and go after "
            "reasoning: why their approach works, and where it stops working."
        )

    # Only when a role is actually stated. Absent data is not evidence that the
    # candidate works outside engineering.
    if role and not builds:
        register = (
            " They do not work in an engineering role, so they came to this "
            "cohort from outside the field. Judge them on what they built and "
            "understood, at the same standard, but drop the insider shorthand and "
            "do not assume production or systems experience."
        )
    else:
        register = ""

    return depth + register


def render_candidate_profile(candidate: Candidate) -> str:
    """The stable, once-per-interview framing: who this is and how they worked."""
    s = candidate.signals
    member = candidate.member
    total = len(candidate.missions)
    skipped = sum(1 for m in candidate.missions if m.skipped)
    ground = sum(1 for m in candidate.missions if m.attempts > 2)

    who = f"You are interviewing {candidate.display_name}"
    if member.job_role:
        who += f", a {member.job_role}"
        if member.years_experience is not None:
            years = member.years_experience
            who += (
                " with no professional experience yet" if years == 0
                else f" with {years} year{'' if years == 1 else 's'} of experience"
            )
    who += ", who just completed a 31-day AI engineering cohort."

    return (
        f"{who}\n\n"
        f"{render_calibration(candidate)}\n\n"
        f"How they worked, across {total} missions:\n"
        f"- Committed code on {s.commit_days} of 31 days\n"
        f"- Completed {s.missions_completed} missions, "
        f"{s.missions_first_try} of them on the first try\n"
        f"- Skipped {skipped} missions outright\n"
        f"- Needed more than two attempts on {ground} missions\n\n"
        "This is background for you only. Never quote these numbers at the "
        "candidate, and never mention their job title or years of experience "
        "back to them. Let it shape the question, not the wording.\n\n"
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


def render_directive(
    mode: QuestionMode,
    move: QuestionKind,
    reason: str,
    *,
    answer_was_thin: bool = True,
) -> str:
    """The instruction for this specific turn -- what to ask and why.

    `answer_was_thin` distinguishes the two reasons a follow-up happens. The
    default is the heuristic case (the answer gave too little to judge) and
    keeps the historical wording exactly. False is the belief-driven case: the
    answer itself held up, but the day is not settled, so telling the model the
    answer was thin would make it react to something that did not happen.
    """
    guidance = MOVE_GUIDANCE.get(move, MOVE_GUIDANCE[QuestionKind.APPLY])

    if mode is QuestionMode.FOLLOW_UP and not answer_was_thin:
        return (
            "That answer held up, but this day is not settled yet -- their "
            "record here leaves real doubt, and one decent answer does not "
            "clear it. Stay on this same day and push one level deeper: a "
            "harder scenario, an edge, a why. Do not move on.\n\n"
            f"{guidance}\n\n"
            "Build directly on what they just said so it reads as raising the "
            "bar, not repeating the question. One question."
        )

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
    answer_was_thin: bool = True,
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
    directive = render_directive(mode, move, reason, answer_was_thin=answer_was_thin)
    messages.append({"role": "user", "content": f"{brief}\n\n---\n\n{directive}"})

    return {"system": SYSTEM_PROMPT, "messages": messages}
