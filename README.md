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
  main.py             FastAPI entrypoint + static mount   (B)
  routes.py           HTTP API surface                    (B)
  session_store.py    In-process session state            (B)
  curriculum.py       Loads curriculum.json at startup    (B)
  question_engine.py  Adaptive question selection         (A)
  feedback_engine.py  Post-interview assessment           (A)
  prompts.py          Prompt templates                    (A)
  signals.py          Learning-signal scoring             (A)
  models.py           Shared Pydantic contracts           (A/B)
  llm.py              Provider wrapper + JSON coercion    (A)
  report.py           Seam from routes to feedback_engine (A/B)
frontend/index.html   Chat UI, served at /                (C)
scripts/              Offline sanity + curriculum checks
tests/                Test suite (134)
curriculum.json       The real 31-day cohort syllabus
```

Both engines grade on one axis. `signals.classify()` turns a mission record into
a `SignalPattern`, `question_engine.priority_for()` turns that into a selection
tier, and `feedback_engine` reports against those same labels -- there is no
second scoring system.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then put a real key in ANTHROPIC_API_KEY
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/> — the chat UI is served by the same app, so that
URL is the demo.

### Provider

The app speaks the Anthropic Messages API, so it runs against Anthropic directly
or against any gateway implementing it. This deployment uses **AgentRouter**,
which needs two variables rather than one:

| Variable | Anthropic directly | AgentRouter |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | your `sk-ant-…` key | your AgentRouter key |
| `ANTHROPIC_BASE_URL` | unset | `https://agentrouter.org` |
| `LLM_MODEL` | any Claude model | `claude-opus-5`, `claude-opus-4-8`, or `gpt-5.6-sol` |

Setting the key without the base URL is the failure worth knowing about: the SDK
sends a gateway key to `api.anthropic.com`, which rejects it. A model the
endpoint does not carry fails differently — HTTP 503, *no available channel for
model* — and only on the first question, so it is worth confirming against
`/v1/models` before changing `LLM_MODEL`.

One capability does **not** carry across providers. Anthropic enforces
`output_config.format` server-side; AgentRouter accepts the field and drops it,
so a schema alone does not guarantee JSON. The end-of-interview report therefore
states its schema in the prompt as well, parses replies that arrive fenced or
wrapped in prose, and retries once with the fault named — see `complete_json` in
`app/llm.py` and `tests/test_llm.py`. Without that, an interview runs all eight
questions and then fails on the report, which is the only response the candidate
actually reads.

`.env` is read at startup by `load_dotenv()` in `app/main.py`, which runs above
the `app.*` imports because `app/llm.py` reads the key at import time. An
exported variable still wins over the file. The startup banner states what was
resolved, so a misconfiguration is visible rather than silent:

```
[config] endpoint : https://agentrouter.org
[config] model    : claude-opus-5
[config] api key  : loaded, 51 chars
```

`api key : MISSING` means the file was not found or the variable is not set.
The key's length is logged; its value never is.

Run the tests and the offline checks without a key:

```bash
pytest -q                              # 134 tests, no network
python scripts/sanity_check.py         # day selection against a stub
python scripts/check_curriculum.py     # curriculum vs the engine's assumptions
```

With a key set, drive a real interview end to end against a running server:

```bash
python scripts/live_interview.py --strong   # high first-try-pass candidate
python scripts/live_interview.py --weak     # several skipped missions
```

## Team

| Role | Area |
| --- | --- |
| A | Question engine, prompts, signal scoring |
| B | FastAPI backend, routes, report assembly |
| C | Frontend interview UI |

## Development log

All AI prompts used to build this project are logged in [PROMPTS.md](PROMPTS.md).

## Licence

[MIT](LICENSE). Security policy and secret-handling notes: [SECURITY.md](SECURITY.md).
