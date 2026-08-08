#!/usr/bin/env python3
"""Sanity-check the question selection logic before wiring it into the API.

    python scripts/sanity_check.py           # stub LLM, no API key needed
    python scripts/sanity_check.py --live    # real Anthropic call

Part 1 exercises `pick_next_day` on its own: no LLM, no conversation, just the
ranking. Part 2 runs a scripted 4-turn interview so you can watch the follow-up
rule fire when an answer comes back thin.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import prompts
from app import question_engine as qe
from app.models import (
    Candidate,
    CohortSignals,
    Curriculum,
    DayTurn,
    InterviewSession,
    Member,
    Mission,
)

CURRICULUM_PATH = Path(__file__).resolve().parent.parent / "curriculum.json"


# --------------------------------------------------------------------------
# Fixture: a candidate with one of every interesting behaviour
# --------------------------------------------------------------------------


def build_candidate() -> Candidate:
    """Deliberately shaped so every selection rule has something to bite on.

    Day numbers and titles are the real ones. The two skipped days and the two
    heavy-struggle days all sit *outside* the flagship modules (3 and 6), so the
    coverage rule has to override the ranking to reach Embeddings/Vector or
    Agentic AI -- which is exactly the case worth testing.
    """
    missions = [
        # Module 1-2: foundations, handled comfortably.
        Mission(day=1, title="VS Code & Python Environment Setup", passed=True, attempts=1),
        Mission(day=2, title="Local LLM & AI Coding Assistant Setup", passed=True, attempts=1),
        Mission(day=3, title="First AI Project, React Frontend & GitHub", passed=True, attempts=2),
        Mission(day=4, title="Reading & Processing Structured Data", passed=True, attempts=1),
        Mission(day=5, title="Reading & Processing Unstructured Data", passed=True, attempts=2),
        Mission(day=6, title="Building the Knowledge Base", passed=True, attempts=2),
        # Module 3 (flagship): mostly steady, one clean first-try pass on day 9.
        Mission(day=7, title="Embeddings Explained", passed=True, attempts=2),
        Mission(day=8, title="Vector Databases Overview", passed=True, attempts=2),
        Mission(day=9, title="Building & Populating the Vector Database", passed=True, attempts=1),
        Mission(day=10, title="The Retrieval & Matching Engine", passed=True, attempts=2),
        # Module 4: ground through function calling, skipped the LoRA lab.
        Mission(day=11, title="RAG End-to-End & LLM API Basics", passed=True, attempts=2),
        Mission(day=12, title="Prompt Engineering Fundamentals", passed=True, attempts=1),
        Mission(day=13, title="Advanced Prompting: Function Calling & Structured Outputs",
                passed=True, attempts=5),
        Mission(day=14, title="Fine-Tuning: Concepts & When to Use It", passed=True, attempts=2),
        Mission(day=15, title="Fine-Tuning: Hands-On with LoRA & QLoRA", skipped=True),
        # Module 5: solid application build.
        Mission(day=16, title="Chatbot Backend & API Integration", passed=True, attempts=2),
        Mission(day=17, title="Chatbot Frontend Development", passed=True, attempts=1),
        Mission(day=18, title="Full-Stack Integration & Streaming Responses", passed=True, attempts=3),
        Mission(day=20, title="Conversation Memory & Context Management", passed=True, attempts=2),
        # Module 6 (flagship): fine, with a first-try pass on MCP.
        Mission(day=21, title="Agentic Frameworks: LangChain Agents & Tool Use", passed=True, attempts=2),
        Mission(day=22, title="Multi-Agent Orchestration", passed=True, attempts=2),
        Mission(day=23, title="Model Context Protocol (MCP)", passed=True, attempts=1),
        Mission(day=24, title="Agentic Chatbot Integration", passed=True, attempts=2),
        # Module 7: never cracked cost optimisation, skipped security entirely.
        Mission(day=25, title="Chatbot Evaluation & Testing", passed=True, attempts=2),
        Mission(day=26, title="Performance Optimization & Cost Management",
                passed=False, attempts=4),
        Mission(day=27, title="Security, Privacy & Guardrails", skipped=True),
        Mission(day=28, title="Docker & Kubernetes Deployment", passed=True, attempts=2),
        # Module 8.
        Mission(day=29, title="Monitoring, Logging & Observability", passed=True, attempts=2),
        Mission(day=31, title="Capstone Project & Final Demo", passed=True, attempts=3),
    ]
    return Candidate(
        member=Member(id="c-001", name="Priya"),
        missions=missions,
        signals=CohortSignals(
            commitDays=26,
            missionsCompleted=sum(1 for m in missions if m.passed),
            missionsFirstTry=sum(1 for m in missions if m.passed and m.attempts == 1),
        ),
    )


# --------------------------------------------------------------------------
# Stub LLM: same surface as anthropic.Anthropic, no network
# --------------------------------------------------------------------------


class StubMessages:
    """Echoes back which day the prompt was built for, proving the brief landed."""

    def create(self, **kwargs):
        brief = kwargs["messages"][-1]["content"]
        day = re.search(r"Day (\d+):", brief)
        follow_up = "Stay on this same day" in brief
        label = "follow-up" if follow_up else "opening"
        text = f"[stub {label} question about day {day.group(1) if day else '?'}]"
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason="end_turn",
        )


class StubAnthropic:
    def __init__(self) -> None:
        self.messages = StubMessages()
        self.beta = SimpleNamespace(messages=StubMessages())


# --------------------------------------------------------------------------
# Part 1: selection only
# --------------------------------------------------------------------------


def show_selection_plan(candidate: Candidate, curriculum: Curriculum, picks: int = 6) -> None:
    print("=" * 72)
    print("PART 1  Day selection (no LLM)")
    print("=" * 72)

    covered: list[int] = []
    for n in range(1, picks + 1):
        pick = qe.pick_next_day(candidate, covered, curriculum)
        if pick is None:
            print(f"\n{n}. exhausted")
            break
        flag = "  <-- COVERAGE OVERRIDE" if pick.forced_coverage else ""
        print(f"\n{n}. Day {pick.day}: {pick.title}{flag}")
        print(f"   tier={pick.priority.name}  pattern={pick.pattern.value}")
        print(f"   {pick.reason}")
        covered.append(pick.day)

    flagship = sorted(qe.FLAGSHIP_DAYS & set(covered[: qe.COVERAGE_DEADLINE]))
    print(
        f"\n   Flagship days within the first {qe.COVERAGE_DEADLINE} picks: "
        f"{flagship or 'NONE -- rule (d) failed'}"
    )


# --------------------------------------------------------------------------
# Part 2: scripted conversation
# --------------------------------------------------------------------------

# Answers keyed by *day*, not turn number, so the transcript stays coherent no
# matter how many follow-ups the engine decides to spend. Index N is the answer
# to the Nth question asked on that day.
SCRIPTED_ANSWERS: dict[int, list[str]] = {
    15: [  # skipped the LoRA lab -- but can clearly reason about it anyway
        "I skipped the hands-on lab, but I get the idea. LoRA freezes the base weights "
        "and trains a pair of low-rank adapter matrices, so you're only updating maybe "
        "1 percent of the parameters. QLoRA quantizes the frozen base to 4-bit on top "
        "of that so it fits on one consumer GPU. I did read through the PEFT docs after.",
    ],
    27: [  # skipped security -- and here it actually shows
        "Honestly not sure, we never really got to that one.",
        "The part I do know is prompt injection. If untrusted text gets concatenated "
        "into the system prompt the model cannot tell instruction from data, so you "
        "want Input Validation on the way in and you keep user text in its own turn.",
    ],
    13: [  # ground out in 5 attempts -- thin first, real detail under probing
        "We used function calling in the chatbot I think.",
        "The schema was a Pydantic model with a required enum for ticket status, and "
        "the model kept passing free text until I set strict to true. OpenAI Function "
        "Calling validates the input against the schema before it comes back.",
    ],
    9: [  # first-try pass on a flagship day -- is it command or luck?
        # Deliberately 29 words: under the length threshold, so the verdict turns
        # on whether the curriculum's own tools[] vocabulary recognises the answer.
        "I used ChromaDB with Sentence Transformers. The bug I hit was embedding "
        "queries with a different model than the documents, so scores were garbage "
        "until both used all-MiniLM-L6-v2.",
    ],
    26: [  # never passed cost optimisation
        "We looked at it but I never got the caching part working properly.",
    ],
}

DEFAULT_ANSWER = "I'm not really sure about that one."


def answer_for(day: int, times_asked: int) -> str:
    """Pick the answer for the Nth question asked on `day`."""
    answers = SCRIPTED_ANSWERS.get(day)
    if not answers:
        return DEFAULT_ANSWER
    return answers[min(times_asked, len(answers) - 1)]


def explain_depth(answer, day_ctx, vocabulary) -> None:
    """Show the depth verdict and the evidence behind it."""
    anchors = qe.specificity_anchors(answer, day_ctx, vocabulary)
    thin = qe.is_shallow(answer, curriculum_day=day_ctx, vocabulary=vocabulary)
    words = len(answer.split())

    if qe.is_non_answer(answer):
        why = "matched a knowledge-disclaimer phrase"
    elif words >= qe.SUBSTANTIAL_WORDS:
        why = f"{words} words >= {qe.SUBSTANTIAL_WORDS}, long enough on its own"
    elif words < qe.MINIMUM_WORDS:
        why = f"{words} words < {qe.MINIMUM_WORDS}, nothing said yet"
    else:
        total = len(anchors["tools"] | anchors["tokens"])
        why = (
            f"{words} words, so the verdict turns on specificity: "
            f"{total} anchors vs {qe.MIN_SPECIFICS} needed"
        )

    print(f"\n  depth: {'SHALLOW' if thin else 'ok'} -- {why}")
    print(f"    from curriculum tools[]: {sorted(anchors['tools']) or '(none)'}")
    print(f"    from token patterns:     {sorted(anchors['tokens']) or '(none)'}")
    print(f"    -> next turn {'probes deeper on this day' if thin else 'moves to a new day'}")


def run_interview(candidate: Candidate, curriculum: Curriculum, client, turns: int) -> None:
    print("\n" + "=" * 72)
    print("PART 2  Scripted interview")
    print("=" * 72)

    session = InterviewSession(candidate=candidate, max_questions=turns)
    asked_on: Counter[int] = Counter()

    for n in range(1, turns + 1):
        question = qe.next_question(session, curriculum, client=client)
        if question is None:
            print(f"\nTurn {n}: interview complete.")
            break

        answer = answer_for(question.day, asked_on[question.day])
        asked_on[question.day] += 1
        day_ctx = curriculum.get(question.day)

        print(f"\n{'-' * 72}")
        print(f"Turn {n}  [{question.mode.value}]  day {question.day}, move={question.move.value}")
        print(f"why: {question.reason}")
        print(f"\n  PROMPT BRIEF -- verbatim, exactly what the model receives:")
        for line in prompts.render_day_brief(day_ctx, candidate.mission_for(question.day)).splitlines():
            print(f"  | {line}")
        print(f"\nQ: {question.reply}")
        print(f"A: {answer}")

        # The engine never mutates the session -- the caller appends the turn.
        session.turns.append(
            DayTurn(
                day=question.day,
                question=question.reply,
                answer=answer,
                follow_up=question.mode.value == "follow_up",
            )
        )
        explain_depth(answer, day_ctx, curriculum.tool_vocabulary())

    print(f"\nDays covered: {session.days_covered}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="use the real Anthropic API")
    parser.add_argument("--turns", type=int, default=6)
    args = parser.parse_args()

    curriculum = Curriculum.load(CURRICULUM_PATH)
    candidate = build_candidate()
    print(f"Loaded {len(curriculum)} curriculum days, {len(candidate.missions)} missions.\n")

    show_selection_plan(candidate, curriculum)

    if args.live:
        from app.llm import get_client

        client = get_client()
        print("\n(using live Anthropic API)")
    else:
        client = StubAnthropic()

    run_interview(candidate, curriculum, client, args.turns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
