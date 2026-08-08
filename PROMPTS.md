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
