# EternityX Interview Agent

An AI interview agent that conducts **personalized, multi-turn technical interviews**
driven by a candidate's real AI Cohort learning signals — attempts, skips, and
first-try passes — and delivers a structured evaluation report.

## Why signals matter

Generic interviews ask everyone the same questions. This agent reads what a
candidate actually struggled with and probes *there*:

| Signal | Interpretation | Interview behaviour |
| --- | --- | --- |
| High attempts, eventual pass | Grinded to a solution; understanding may be shallow | Probe the *why*, not the *what* |
| Skipped | Avoided the topic — gap or low confidence | Start easy, scaffold upward |
| First-try pass | Genuine fluency | Skip basics, push to depth/edge cases |

## Architecture

```
app/
  main.py            FastAPI entrypoint            (B)
  routes.py          HTTP API surface              (B)
  question_engine.py Adaptive question selection   (A)
  prompts.py         Prompt templates              (A)
  signals.py         Learning-signal scoring       (A)
  models.py          Shared Pydantic contracts     (A/B)
  llm.py             LLM client wrapper            (A)
  report.py          Structured final report       (B)
frontend/            Interview UI                  (C)
tests/               Test suite
data/                Sample signal fixtures
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # add your ANTHROPIC_API_KEY
uvicorn app.main:app --reload
```

## Team

| Role | Area |
| --- | --- |
| A | Question engine, prompts, signal scoring |
| B | FastAPI backend, routes, report assembly |
| C | Frontend interview UI |

## Development log

All AI prompts used to build this project are logged in [PROMPTS.md](PROMPTS.md).
