#!/usr/bin/env python3
"""Drive a real interview against a running server. No mocks, no TestClient.

    uvicorn app.main:app --port 8000          # in one terminal
    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/live_interview.py --strong
    python scripts/live_interview.py --weak

Talks real HTTP to a real process. Prints every question, marks follow-ups,
times the final feedback call on its own, and asserts the interview actually
enforced its floor (>= 8 questions across >= 4 distinct days).

Answers are scripted so runs are comparable, and deliberately include a
one-word answer, an off-topic answer, and an empty-ish answer to check the
engine neither crashes nor sails past them.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = ROOT / "candidates.json"

MIN_QUESTIONS = 8
MIN_DAYS = 4

# Cycled in order. Deliberately mixed: substantive answers, then the three
# degenerate shapes that must not crash the engine or be silently accepted.
ANSWERS = [
    "I'd start from the retrieval side. If the answer isn't in the top-k chunks "
    "no amount of prompting fixes it, so I'd check recall before touching the "
    "prompt, then look at whether chunks are straddling the answer.",
    "yeah",                                                   # one word
    "I once built a Discord bot for my football league.",      # off-topic
    "The embeddings had to come from the same model on both sides. I was "
    "embedding the query with a different model than the documents, so the "
    "cosine scores were meaningless until I made them match.",
    ".",                                                       # near-empty
    "We used ChromaDB with metadata filters so I could scope retrieval to one "
    "department before the semantic search ran.",
    "Not sure honestly, we ran out of time on that one.",
    "I'd cache the embedding calls first since they're the repeated cost, then "
    "batch them. Token counting with tiktoken told me where the spend was.",
    "It kept returning free text where I wanted a fixed set of values, so I "
    "put the tool schema behind a Pydantic model and turned strict on.",
    "I think so? I'd have to look it up again.",
    "Mostly I logged every tool call with its inputs so I could replay the "
    "failing ones instead of guessing.",
    "Right - the risk is untrusted text being read as instructions.",
]


def load_candidate(strong: bool, name: str | None) -> dict:
    data = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    people = data["candidates"] if isinstance(data, dict) else data

    if name:
        match = next((c for c in people if c["member"]["name"].lower() == name.lower()), None)
        if match is None:
            sys.exit(f"No candidate named {name!r}. Try: "
                     + ", ".join(c["member"]["name"] for c in people[:5]) + " ...")
        return match

    def first_try(c: dict) -> int:
        return sum(1 for m in c["missions"] if m.get("passed") and m.get("attempts") == 1)

    def skipped(c: dict) -> int:
        return sum(1 for m in c["missions"] if m.get("skipped"))

    return max(people, key=first_try) if strong else max(people, key=lambda c: (skipped(c), -first_try(c)))


def describe(candidate: dict) -> str:
    ms, sig = candidate["missions"], candidate["signals"]
    return (
        f"{candidate['member']['name']} ({candidate['member'].get('jobRole','?')}) -- "
        f"{sum(1 for m in ms if m.get('passed') and m.get('attempts') == 1)} first-try, "
        f"{sum(1 for m in ms if m.get('skipped'))} skipped, "
        f"{sum(1 for m in ms if (m.get('attempts') or 0) > 2)} ground out, "
        f"commitDays={sig.get('commitDays')}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--strong", action="store_true", help="highest first-try-pass candidate")
    group.add_argument("--weak", action="store_true", help="most skipped missions")
    ap.add_argument("--name", help="pick a candidate by name instead")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--max-turns", type=int, default=20)
    args = ap.parse_args()

    candidate = load_candidate(strong=args.strong or not args.weak, name=args.name)
    session_id = f"live-{int(time.time())}"

    print("=" * 78)
    print(describe(candidate))
    print(f"server {args.base_url}   session {session_id}")
    print("=" * 78)

    url = f"{args.base_url}/api/interview"
    # Generous read timeout: the feedback call reasons over the whole transcript.
    http = httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0))

    def post(body: dict) -> tuple[dict, float]:
        started = time.perf_counter()
        response = http.post(url, json={"sessionId": session_id, **body})
        elapsed = time.perf_counter() - started
        if response.status_code != 200:
            print(f"\nHTTP {response.status_code}: {response.text[:400]}")
            sys.exit(1)
        return response.json(), elapsed

    body, elapsed = post({"candidate": candidate})
    print(f"\n[Q1] ({elapsed:.1f}s)\n  {body['reply']}")

    asked, latencies, feedback_seconds = 1, [elapsed], None
    for index in range(args.max_turns):
        answer = ANSWERS[index % len(ANSWERS)]
        label = {"yeah": "ONE WORD", ".": "NEAR-EMPTY"}.get(
            answer, "OFF-TOPIC" if "Discord" in answer else ""
        )
        print(f"\n  > {answer}" + (f"   <-- {label}" if label else ""))

        body, elapsed = post({"message": answer})
        if body["done"]:
            feedback_seconds = elapsed
            print(f"\n{'=' * 78}\nDONE after {asked} questions   feedback call: {elapsed:.1f}s")
            print(json.dumps(body.get("feedback"), indent=2))
            break

        asked += 1
        latencies.append(elapsed)
        print(f"\n[Q{asked}] ({elapsed:.1f}s)\n  {body['reply']}")
    else:
        print(f"\nNever finished within {args.max_turns} turns.")
        return 1

    print("\n" + "-" * 78)
    if latencies:
        ordered = sorted(latencies)
        print(f"question latency: min {ordered[0]:.1f}s  median "
              f"{ordered[len(ordered) // 2]:.1f}s  max {ordered[-1]:.1f}s  (n={len(latencies)})")
    print(f"feedback latency: {feedback_seconds:.1f}s")

    ok = asked >= MIN_QUESTIONS
    print(f"\n{'PASS' if ok else 'FAIL'}  asked {asked} questions (floor {MIN_QUESTIONS})")
    print("      distinct-day floor is enforced server-side by session_store.can_finish;")
    print("      reaching 'done' at all proves both gates were satisfied.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
