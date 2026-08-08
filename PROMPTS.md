# Prompt Log

A chronological record of every AI prompt used while building the EternityX
Interview Agent. Logged in real time, before and during implementation.

---

## Entry 001 — Project kickoff & scaffold
**Date:** 2026-08-08
**Author:** A (Shivanshu)
**Tool:** Claude Code (Opus 5)
**Stage:** 1 — Setup

### Prompt

> Create the GitHub repo — public, init with README. Do this first, this
> timestamp matters for Stage 2 authenticity.
> Push an empty/minimal scaffold immediately — folder structure only, even empty
> files. First commit history point.
> Share repo with B and C, get them cloned.
> Set up your local env: Python 3.11+, venv, install fastapi, uvicorn, anthropic
> (or openai).
> You start on your piece: question_engine.py + prompts.py — since you own the
> hardest part (the "thoughtful idea" logic), start there so B has something real
> to import instead of a stub soon.
> Log your first PROMPTS.md entry now, before you even start coding — this
> instruction itself counts.

### Intent

Lock in an auditable commit history from minute zero, unblock B and C with a
real directory contract they can import against, and stand up the local
toolchain before any feature work begins.

### Outcome

- Public repo confirmed (created 2026-08-08T10:12:52Z).
- Scaffold committed: `app/` package with owner-annotated module stubs,
  `frontend/`, `tests/`, `data/`, plus `requirements.txt`, `.gitignore`,
  `.env.example`, and this log.
- Module ownership assigned in README so parallel work does not collide.

### Notes

The scaffold intentionally ships *named, owner-tagged* empty modules rather than
a single placeholder file. The filenames themselves are the interface contract —
B can write `from app.question_engine import QuestionEngine` before the
implementation lands.

---

## Entry 002 - Question engine integration contract
**Date:** 2026-08-08
**Author:** B (Nirbhay)
**Tool:** OpenAI Codex (GPT-5.6 sol High)
**Stage:** 2 - Backend integration

### Prompt

> Integrate the FastAPI interview session layer with the adaptive question
> engine contract. Load `curriculum.json` once per application process and pass
> it to `next_question(session, curriculum, client=None)`. Extend the shared
> question-result model to accept the engine's tier, pattern, reason, and
> follow-up metadata, while remaining forward-compatible with additional
> metadata. Persist that metadata with each interviewer history entry so future
> follow-up prompts retain the rationale behind prior questions. Preserve the
> existing eight-question/four-day completion gate and verify the integration
> through automated tests.

### Intent

Align Member B's API and session-state boundary with Member A's adaptive
question engine without coupling the route to the engine's internal logic.

### Outcome

- Curriculum is loaded once and supplied to every question-engine call.
- Rich question-selection metadata is validated and retained in session history.
- The placeholder engine now matches the production engine signature.
- Integration and hard-gate behavior remain covered by automated tests.

### Notes

The API remains the sole owner of session mutations. The question engine receives
a defensive copy and returns structured question data.

---

## Entry 003 — Adaptive question engine and prompt templates
**Date:** 2026-08-08
**Author:** A (Shivanshu)
**Tool:** Claude Code (Opus 5)
**Stage:** 2 — Core engine

### Prompt

> I'm building a FastAPI backend for an AI Interview Agent hackathon project.
>
> Context: This agent conducts personalized technical interviews for candidates
> who completed a 31-day AI engineering cohort. It picks questions based on the
> candidate's real performance data (attempts, skips, first-try passes) rather
> than asking generic questions.
>
> I need you to build `question_engine.py` and `prompts.py`.
>
> Data I have:
> - curriculum.json: 31 days, each with { day, title, type, tools, objectives }
> - candidate object per session: { member: {...}, missions: [{ day, title,
>   passed, attempts, skipped }], signals: { commitDays, missionsCompleted,
>   missionsFirstTry } }
>
> Requirements for question_engine.py:
> 1. `pick_next_day(candidate, days_covered)` — given the candidate's missions
>    and days already asked, return the next curriculum day to probe. Priority
>    order: (a) skipped missions first — ask them to explain despite skipping,
>    (b) missions with attempts > 2 (struggled, worth probing deeper),
>    (c) missions with attempts == 1 on high-value topics (verify it wasn't luck),
>    (d) ensure at least one of days 21-24 (Agentic AI/MCP) or 7-10
>    (Embeddings/Vector) gets covered by question 4.
> 2. `next_question(session, curriculum)` — builds the LLM prompt for the next
>    turn: injects current day's objectives, candidate's performance on that day,
>    last 2-3 turns of conversation history, and calls the LLM to generate ONE
>    interview question or follow-up. If the candidate's last answer was shallow,
>    generate a probing follow-up on the SAME day instead of moving on.
>
> Requirements for prompts.py:
> - A SYSTEM_PROMPT constant: instructs the LLM to behave like a real senior
>   technical interviewer — conversational, adaptive, reacts genuinely to what
>   the candidate says, asks ONE question at a time, never lectures, never lists
>   multiple questions in one reply.
> - A function to build the full message payload for the LLM call given session
>   state + current day's curriculum context.
>
> Use the Anthropic Python SDK (client.messages.create). Keep functions pure
> and testable — no global state, everything takes session/candidate/curriculum
> as arguments. Write clean, well-commented code since teammates need to read it.
>
> Also give me a small test script that simulates a candidate answering 3-4
> turns so I can sanity check the question selection logic before wiring it
> into the API.

### Intent

Build the differentiating logic of the project: question selection driven by a
candidate's actual cohort behaviour rather than a fixed question bank.

### Outcome

- `question_engine.py`: `pick_next_day` with four priority tiers, `next_question`
  with same-day follow-up on shallow answers, deterministic ranking.
- `prompts.py`: interviewer persona plus payload assembly.
- `llm.py` and day/mission models added — both were prerequisites, not scope creep.
- `data/curriculum.json` generated as a placeholder (the real file was not
  supplied), plus `scripts/sanity_check.py` and 33 tests.

### Notes

Two judgement calls worth recording. Rule (b) was widened to include missions
attempted but never passed regardless of attempt count — a failed two-attempt
mission is as much a known gap as a five-attempt one. And rather than write a
second classifier, `Mission` was bridged into the existing `signals.classify()`
so one set of rules decides what counts as a grinder or an avoider.

---

## Entry 004 — Model choice, real data, single schema
**Date:** 2026-08-08
**Author:** A (Shivanshu)
**Tool:** Claude Code (Opus 5)
**Stage:** 2 — Core engine

### Prompt

> Two fixes before we move on:
>
> 1. app/llm.py — the model string is wrong, "Opus 5" doesn't exist. Use
>    "claude-sonnet-5" instead — it's fast enough for single-question generation
>    and we don't need Opus-tier reasoning for this call. Update the model
>    string and re-check the effort/latency settings still make sense for
>    Sonnet.
> 2. data/curriculum.json — replace this with the real curriculum file, don't
>    use the placeholder you generated. I'm providing the actual 31-day
>    curriculum JSON now (attaching/pasting it below) — swap it in as-is, and
>    confirm nothing in question_engine.py or prompts.py assumes field names or
>    structure that differ from the real file.
> 3. models.py — collapse the two shapes. Drop the topic-based one, keep only
>    the day/mission shape since that's what this request needs. Confirm
>    signals.classify() still works cleanly against the single shape.

### Intent

Settle the model tier, move onto real curriculum data, and remove the duplicate
data model before it calcified.

### Outcome

- Switched to `claude-sonnet-5`. Effort raised `low` → `medium` and `max_tokens`
  1500 → 4000, because Sonnet 5 honours effort strictly at the low end and
  adaptive thinking shares the token budget with the answer.
- `models.py` collapsed to one shape; `classify()` now takes a `Mission`
  directly, deleting the bridge and two wrapper functions.
- **Curriculum not swapped** — no file arrived with the message. Delivered a
  compatibility contract instead so the swap would be one step later.

### Notes

Claude Opus 5 does exist, but the reasoning behind the switch held anyway: this
call generates two or three sentences and does not need Opus-tier reasoning.
The collapse also corrected a latent bug — a mission with zero attempts,
no pass and no skip now classifies as UNKNOWN rather than STRUGGLED. Absence of
evidence is not evidence of a gap.

---

## Entry 005 — Real curriculum swap and verification
**Date:** 2026-08-08
**Author:** A (Shivanshu)
**Tool:** Claude Code (Opus 5)
**Stage:** 2 — Core engine

### Prompt

> The real curriculum.json is at /mnt/user-data/uploads/curriculum.json in this
> conversation, or I'll paste it directly. Confirm receipt then:
>
> 1. Replace data/curriculum.json with the real file
> 2. Run scripts/check_curriculum.py to verify AGENTIC_DAYS (21-24) and
>    VECTOR_DAYS (7-10) actually match — spoiler, they should, the real
>    curriculum uses the same day ranges as your placeholder guessed
> 3. Confirm the schema matches what your code expects: top-level {"cohort",
>    "modules", "days"} where each day has {day, title, type, tools[],
>    objectives[]} — flag any field name mismatch
> 4. Re-run your simulated turns test against the real data and show me output

### Intent

Replace invented fixture data with the genuine 31-day syllabus and prove the
engine's assumptions survive contact with it.

### Outcome

- Real file located at `~/Downloads/curriculum.json` (the `/mnt` path does not
  exist in this environment) and verified against the described schema before use.
- Day-level schema an exact match. Top-level `cohort` and `modules` were being
  silently dropped by pydantic; both are now captured.
- Flagship bands confirmed, and the checker rewritten to verify them against the
  file's own module ranges instead of guessing from titles.

### Notes

The key finding: `AGENTIC_DAYS` and `VECTOR_DAYS` are exactly modules 6 and 3.
The bands are module boundaries, not arbitrary constants. If the syllabus is ever
renumbered, rule (d) keeps firing on schedule but probes the wrong days, with no
error and no failing test — which is why the checker now cross-references
`modules[].day_range` directly.

---

## Entry 006 — Real prompt text and vocabulary verification
**Date:** 2026-08-08
**Author:** A (Shivanshu)
**Tool:** Claude Code (Opus 5)
**Stage:** 2 — Core engine

### Prompt

> Good work on the module-boundary discovery — log that reasoning yourself,
> I'll add my own note to PROMPTS.md separately.
>
> Now run the simulated turns test again against the REAL curriculum data and
> show me the output — want to see actual question/objective text this time,
> not placeholder text, so I can sanity check the answer-depth vocabulary is
> pulling from real tools[] entries.

### Intent

Verify the answer-depth heuristic scores against the curriculum's genuine
`tools[]` vocabulary rather than invented tool names.

### Outcome

- Sanity check now prints the rendered prompt brief verbatim and splits depth
  anchors by source, so scoring is auditable rather than a number to trust.
- Confirmed real matches: `lora`/`peft`/`qlora` on day 15, `input validation`
  on day 27, `chromadb`/`sentence transformers` on day 9.

### Notes

Running against real data exposed that a scripted answer referenced `pgvector`,
which this cohort never used — they used ChromaDB. Plausible-sounding synthetic
data contradicted by the real file, the same failure class as the placeholder
curriculum. Two heuristic bugs surfaced in the same run: "I skipped the lab,
but…" was being classified as a non-answer even though tier (a) exists to ask
about skipped missions, and the specificity regex could not see `HNSW`, `p95`
or `200ms`.

---

## Entry 007 — Word-boundary tool matching
**Date:** 2026-08-08
**Author:** A (Shivanshu)
**Tool:** Claude Code (Opus 5)
**Stage:** 2 — Core engine

### Prompt

> fix the substring matching to use word boundaries

### Intent

Stop tool names matching inside unrelated words when scoring answer depth.

### Outcome

- Naive `term in answer` replaced with lookaround-anchored patterns.
- Longer matched names now absorb shorter ones they contain, and tokens that are
  fragments of an already-credited tool are dropped, so the two anchor sets are
  disjoint.

### Notes

Measured against ordinary prose containing no real tool mentions, the old
matcher found six anchors — `cline` in "decline", `git` in "legitimate",
`react` in "reaction", `vite` in "invite", `sql` in "sqlalchemy". Since a
mid-length answer needs only two anchors to clear the depth check, a completely
vague answer could pass on English coincidence, suppressing exactly the
follow-ups the engine exists to ask. Larger than the double-counting originally
flagged. Note the absorption rule keys on containment, not length: LoRA and
QLoRA both survive, because neither contains the other at a word boundary.

---

## Entry 008 — Merge with the API layer
**Date:** 2026-08-08
**Author:** A (Shivanshu)
**Tool:** Claude Code (Opus 5)
**Stage:** 3 — Integration

### Prompt

> Time to validate live and merge with B's API layer.
>
> 1. Run the live sanity check:
>    export ANTHROPIC_API_KEY=sk-ant-...
>    python scripts/sanity_check.py --live
>    Show me the actual generated questions for 3-4 turns.
> 2. Pull B's latest work from main and reconcile the contract mismatch:
>    - B's next_question(session) takes only session and returns {reply, day}
>    - My real next_question(session, curriculum, client=None) needs curriculum
>      passed in, and returns a NextQuestion with day, reply, tier, pattern,
>      reason, is_follow_up — and does NOT mutate session, the caller appends
>      the turn
>    Update app/models.py's QuestionResult to carry tier/pattern/reason, not
>    just reply+day. Update app/routes.py to load curriculum.json once at
>    startup and thread it through to next_question(). Update _record_question()
>    in routes.py to append using the real field names.
> 3. Run both test suites together (B's existing tests + my 53) and confirm
>    everything passes against the merged code, not in isolation.
> 4. Show me a diff summary of exactly what changed in models.py and routes.py
>    before we commit.

### Intent

Bring the engine and the API together on one branch and prove both suites pass
against merged code rather than in isolation.

### Outcome

- Merged `origin/main` onto a feature branch; conflicts in `models.py` (resolved
  as a union) and `question_engine.py` (engine wins, as B's stub intended).
- `models.py`: +21 types, none of B's four removed. Dict-session adapters added.
- **`routes.py`: zero lines changed.** 91 tests pass together.
- Live run not performed — no Anthropic key available.

### Notes

The described mismatch was one commit stale: B's `9a65387` had already aligned
the contract, so all three requested edits were already done. The real gap was
structural, not naming — B stores sessions as dicts, the engine takes typed
models — so the engine was adapted to B's contract rather than the reverse.
The merge also exposed a genuine bug: unbounded follow-up probing meant eight
questions covered only three distinct days, so `can_finish` could never fire for
a terse candidate. Follow-ups are now budgeted against remaining breadth.

---

## Entry 009 — Commit and open the pull request
**Date:** 2026-08-08
**Author:** A (Shivanshu)
**Tool:** Claude Code (Opus 5)
**Stage:** 3 — Integration

### Prompt

> ok do it

### Intent

Commit the reviewed merge and open it for B rather than pushing to main.

### Outcome

- Merge committed on `member-a-engine`, pushed, PR #3 opened against `main`.
- `main` untouched; nothing force-pushed.

### Notes

Kept to B's existing PR workflow. The PR body leads with the three things a
reviewer needs to weigh: that `routes.py` is unchanged, the starvation bug and
its fix, and the two edits made to B's tests.

---

## Entry 010 — Feedback engine and frontend
**Date:** 2026-08-08
**Author:** A (Shivanshu)
**Tool:** Claude Code (Opus 5)
**Stage:** 4 — Feature completion

### Prompt

> Two things, both blocking:
>
> 1. feedback_engine.py — real implementation. Takes the full session history
>    (candidate + all Q&A turns) and generates summary/strengths/gaps/next via
>    one LLM call. Replace the placeholder in report.py. Use the same
>    candidate signal logic (skipped/struggled/fluent) the question engine
>    already tracks, so feedback references the SAME tiering the interview
>    was built around — don't invent a separate scoring system.
> 2. frontend/ — minimal chat interface hitting POST /api/interview. Doesn't
>    need to be pretty, needs to work: text input, message history display,
>    handles the start/turn/done response shapes from the spec. This becomes
>    our Live URL homepage and demo.

### Intent

Close the loop: an assessment graded on the same axis the interview was built
on, and a usable demo surface.

### Outcome

- `feedback_engine.py`: one structured-output call producing the spec's four
  fields. Every day in the evidence block carries the `Priority` tier that made
  the engine pick it and the `SignalPattern` `classify()` derived.
- `llm.complete_json()` added so the response is guaranteed to match the schema.
- `report.generate(session) -> dict` unchanged, so `routes.py` stayed untouched.
- `frontend/index.html`: single file, no build step, no CDN, mounted at `/`.
- 111 tests pass.

### Notes

The framing that makes the feedback worth reading: it is the comparison of prior
belief against interview evidence. A skipped mission the candidate can still
explain is a scheduling gap; a passed mission they cannot explain is a knowledge
gap. Tests assert every tier and pattern label is a value of the engine's own
enums, so a third vocabulary cannot drift in later.

---

## Entry 011 — Live end-to-end validation attempt
**Date:** 2026-08-08
**Author:** A (Shivanshu)
**Tool:** Claude Code (Opus 5)
**Stage:** 5 — Validation

### Prompt

> Time for the real thing — no more mocks anywhere in the loop.
>
> 1. Set ANTHROPIC_API_KEY and run a genuine end-to-end interview through
>    the deployed/local FastAPI server (not test client, not sanity_check.py
>    — the actual running app), using two real candidates.json entries: one
>    strong (high first-try passes) and one weak (several skipped missions).
> 2. Go through the actual frontend/index.html in a browser, not curl — click
>    through the full 8-question flow for at least one candidate, watch the
>    feedback render.
> 3. Paste me: the real questions asked, at least one real follow-up
>    triggered by a thin answer, and the final feedback JSON.
> 4. Time the final feedback call specifically — I want the actual latency
>    number so we know if the "Thinking..." wait needs a real fix or just
>    better copy.
>
> Confirm: 8+ questions across 4+ days actually enforced, follow-ups reference
> real prior answers, final feedback is specific not generic, no crashes on a
> deliberately bad/short/off-topic answer.

### Intent

Prove the whole system works against the real API, with real candidates, in a
real browser.

### Outcome

- **Blocked: no Anthropic credential available.** No live LLM output was
  produced, and none was fabricated. The latency question remains unanswered —
  it can only be measured.
- `candidates.json` located; strong (Diane Foster, 10/10 first-try) and weak
  (Mia Alvarez, 5 skipped) candidates identified.
- Verified for real: uvicorn serving over HTTP, the page driven in Chromium,
  static mount not shadowing the API, error rendering, input gating.
- `scripts/live_interview.py` written so the run is one command once a key exists.

### Notes

Running the real server found two bugs the test suite could not. A missing
credential surfaces from the SDK as a `TypeError` at request time, which the
fallback path was swallowing as "this SDK does not support the beta" —
permanently disabling refusal fallbacks over an unrelated deployment problem,
and reporting "Question engine returned an invalid result" to the client.
Separately, `margin: 0 auto` on the candidate editor shrink-wrapped it inside
the flex column, rendering it at a third of its intended width. Neither is
reachable from a test client.

---

## Entry 012 — Backfill the prompt log
**Date:** 2026-08-08
**Author:** A (Shivanshu)
**Tool:** Claude Code (Opus 5)
**Stage:** 5 — Validation

### Prompt

> update promts.md all the promts

### Intent

Restore the log's completeness. Entries 003–011 had been developed but not
recorded, leaving a gap between the scaffold and the finished engine.

### Outcome

- Entries 003–012 appended, each carrying the verbatim prompt, intent, outcome,
  and the engineering notes worth keeping.

### Notes

Entries 003–011 are backfilled from the working session rather than written at
the time, and are marked as such here rather than presented as real-time logging.
Blocked steps are recorded as blocked — the curriculum file in entry 004 and the
API key in entry 011 — because a log that only records successes is not an audit
trail.

---
