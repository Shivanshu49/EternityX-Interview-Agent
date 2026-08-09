"""Drive the adaptive-eval path end to end against the real provider.

    ENABLE_ADAPTIVE_EVAL=1 python scripts/live_adaptive.py

In-process (TestClient) rather than over HTTP, because the point is to inspect
what the API never exposes: the grades stored on each answer and the belief
trajectory they produce. Asserts the wire contract stayed {reply, done}, the
completion gates held, grades were persisted, and no em dash leaked into a
question. Exit code 0 only if all of that held.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import evaluation  # noqa: E402
from app.curriculum import CURRICULUM  # noqa: E402
from app.main import app  # noqa: E402
from app.models import session_from_dict  # noqa: E402
from app.session_store import sessions  # noqa: E402
from app.signals import build_profiles, fold_beliefs  # noqa: E402

if not evaluation.ENABLE_ADAPTIVE_EVAL:
    sys.exit("run with ENABLE_ADAPTIVE_EVAL=1 -- this drives the graded path")

people = json.load(open(ROOT / "candidates.json"))
people = people["candidates"] if isinstance(people, dict) else people
who = max(people, key=lambda c: sum(1 for m in c["missions"] if m.get("skipped")))
print(f"candidate: {who['member']['name']} ({who['member']['jobRole']})")

# The mix that matters: substantive, precise-but-short, confidently wrong, blank.
ANSWERS = [
    "I'd check retrieval recall first. If the answer isn't in the top-k chunks, "
    "no prompt change recovers it, so recall before prompt, then chunk overlap.",
    "Same embedding model on both sides, or cosine scores are meaningless.",
    "For retrieval you always want the biggest chunks possible because more "
    "context means the embedding captures more meaning, and cosine similarity "
    "works best on exact keyword matches, so I would raise chunk size to the "
    "maximum and duplicate the query keywords into every chunk.",
    ".",
    "We used ChromaDB with metadata filters to scope one department before the "
    "semantic search ran, which cut false positives.",
    "I put the tool schema behind a Pydantic model with strict mode on, so free "
    "text where an enum belongs fails validation instead of executing.",
    "Not sure honestly, we ran out of time on that one.",
    "I'd cache and batch the embedding calls, they're the repeated cost. "
    "tiktoken told me where the spend was.",
    "Log every tool call with inputs so failing ones can be replayed.",
    "The risk is untrusted retrieved text being read as instructions, prompt "
    "injection through the corpus itself.",
    "Compare recall@5 on a labelled eval set before and after the swap.",
    "Overlap chunks about 15 percent on section boundaries.",
]

client = TestClient(app)
sid = f"adaptive-{int(time.time())}"
URL = "/api/interview"

t0 = time.perf_counter()
r = client.post(URL, json={"sessionId": sid, "candidate": who})
lat = [time.perf_counter() - t0]
body = r.json()
assert set(body) == {"reply", "done"}, f"contract violated: {sorted(body)}"
print(f"\n[Q1] ({lat[-1]:.1f}s) {body['reply'][:110]}")

failures: list[str] = []
for i, answer in enumerate(ANSWERS):
    t0 = time.perf_counter()
    r = client.post(URL, json={"sessionId": sid, "message": answer})
    lat.append(time.perf_counter() - t0)
    body = r.json()
    if r.status_code != 200:
        failures.append(f"turn {i + 1} -> HTTP {r.status_code}: {body}")
        break
    if body["done"]:
        keys = set(body)
        assert keys == {"reply", "done", "feedback"}, keys
        print(f"\nDONE after answer {i + 1}   feedback keys ok   ({lat[-1]:.1f}s)")
        break
    assert set(body) == {"reply", "done"}, f"contract violated: {sorted(body)}"
    print(f"\n  > {answer[:80]}")
    print(f"[Q ] ({lat[-1]:.1f}s) {body['reply'][:110]}")

state = sessions[sid]
graded = [
    (h["content"][:55], h["evaluation"])
    for h in state["history"]
    if h["role"] == "candidate" and "evaluation" in h
]
followups = [h for h in state["history"] if h["role"] == "interviewer" and h.get("is_follow_up")]

print("\n" + "=" * 74)
print(f"questions asked: {state['questions_asked']}   distinct days: {sorted(set(state['days_covered']))}")
print(f"graded answers stored: {len(graded)}   follow-ups: {len(followups)}")
print("=" * 74)

for text, ev in graded:
    print(f"  {ev['score']:.2f} {ev['level']:<12} {text!r}")

# The belief trajectory the report will eventually use.
typed = session_from_dict(state)
beliefs = fold_beliefs(build_profiles(typed.candidate, CURRICULUM), typed.turns)
moved = {d: p for d, p in beliefs.items() if d in set(state["days_covered"])}
print("\nbelief after interview (prior -> posterior):")
priors = build_profiles(typed.candidate, CURRICULUM)
for d, p in sorted(moved.items()):
    prior = priors.get(d)
    before = f"{prior.mastery:.2f}/{prior.uncertainty:.2f}" if prior else "unknown"
    print(f"  day {d:>2} {p.pattern:<10} {before} -> {p.mastery:.2f}/{p.uncertainty:.2f}")

ordered = sorted(lat)
print(f"\nlatency: min {ordered[0]:.1f}s median {ordered[len(ordered)//2]:.1f}s max {ordered[-1]:.1f}s")

ok = (
    not failures
    and state["questions_asked"] >= 8
    and len(set(state["days_covered"])) >= 4
    and len(graded) >= 4
)
dash = re.compile(r"[—–―]")
dash_hits = [h["content"] for h in state["history"] if h["role"] == "interviewer" and dash.search(h["content"])]
if dash_hits:
    failures.append(f"em dash leaked into {len(dash_hits)} question(s)")
    ok = False

for f in failures:
    print("FAIL:", f)
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
