"""LLM wrapper tests, with the JSON path as the load-bearing case.

`output_config.format` is enforced by api.anthropic.com and silently dropped by
Messages-API gateways such as AgentRouter, which this project runs against. So
the requirement is not "structured output works" -- it is that the end-of-
interview report still parses when the endpoint ignores the schema entirely and
answers in prose. That is the failure these tests exist to prevent, because it
lands on the one response a candidate actually reads.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from app import llm


SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

PAYLOAD = {"system": "You are a reviewer.", "messages": [{"role": "user", "content": "Go."}]}

VALID = {"summary": "It went well."}


class ScriptedClient:
    """Replays `replies` in order, recording each request it was given."""

    def __init__(self, *replies: str, stop_reason: str = "end_turn") -> None:
        self.replies = list(replies)
        self.stop_reason = stop_reason
        self.calls: list[dict] = []
        self.messages = self
        self.beta = SimpleNamespace(messages=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason=self.stop_reason,
        )


class RawStringClient(ScriptedClient):
    """Gateway shape observed in production: create() returns text directly."""

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


# --------------------------------------------------------------------------
# Extracting an object from an unconstrained reply
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        '{"summary": "ok"}',
        '```json\n{"summary": "ok"}\n```',
        '```\n{"summary": "ok"}\n```',
        'Here is the assessment:\n{"summary": "ok"}\nLet me know if you need more.',
        '  \n {"summary": "ok"}  \n ',
    ],
    ids=["bare", "json_fence", "bare_fence", "wrapped_in_prose", "whitespace"],
)
def test_json_object_is_recovered_from_an_unconstrained_reply(reply):
    assert llm._extract_json_object(reply) == {"summary": "ok"}


def test_nested_braces_survive_the_outermost_brace_fallback():
    reply = 'Report:\n{"summary": "ok", "detail": {"day": 9}}\nEnd.'
    assert llm._extract_json_object(reply)["detail"] == {"day": 9}


@pytest.mark.parametrize(
    "reply",
    ["The candidate did well.", "", "[1, 2, 3]", '"just a string"', "{not json at all}"],
    ids=["prose", "empty", "array", "string", "malformed"],
)
def test_unparseable_reply_raises(reply):
    with pytest.raises(llm.LLMError):
        llm._extract_json_object(reply)


# --------------------------------------------------------------------------
# The schema reaches the model two ways, because only one is always honoured
# --------------------------------------------------------------------------


def test_schema_is_restated_in_the_system_prompt():
    """A gateway that drops output_config still has to see the contract."""
    merged = llm._with_json_directive(PAYLOAD, SCHEMA)

    assert merged["system"].startswith("You are a reviewer.")
    assert "single JSON object" in merged["system"]
    assert '"additionalProperties": false' in merged["system"]
    assert merged["messages"] == PAYLOAD["messages"], "messages must be untouched"


def test_directive_survives_a_payload_with_no_system_prompt():
    merged = llm._with_json_directive({"messages": []}, SCHEMA)
    assert "single JSON object" in merged["system"]


def test_output_config_is_still_sent_so_anthropic_enforces_it():
    """Dropping the native path would regress the first-party provider."""
    client = ScriptedClient(json.dumps(VALID))
    llm.complete_json(PAYLOAD, SCHEMA, client=client)

    fmt = client.calls[0]["output_config"]["format"]
    assert fmt == {"type": "json_schema", "schema": SCHEMA}


def test_original_payload_is_not_mutated():
    before = json.dumps(PAYLOAD, sort_keys=True)
    llm.complete_json(PAYLOAD, SCHEMA, client=ScriptedClient(json.dumps(VALID)))
    assert json.dumps(PAYLOAD, sort_keys=True) == before


def test_plain_completion_accepts_a_gateway_string_response():
    assert llm.complete(PAYLOAD, client=RawStringClient("Gateway question.")) == (
        "Gateway question."
    )


def test_structured_completion_accepts_a_gateway_string_response():
    client = RawStringClient(json.dumps(VALID))
    assert llm.complete_json(PAYLOAD, SCHEMA, client=client) == VALID


def test_custom_gateway_skips_anthropic_only_beta(monkeypatch):
    client = ScriptedClient("Gateway question.")
    beta_calls: list[dict] = []

    class BetaMessages:
        def create(self, **kwargs):
            beta_calls.append(kwargs)
            raise AssertionError("custom gateways must not receive Anthropic beta fields")

    client.beta = SimpleNamespace(messages=BetaMessages())
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://co.agentrouter.org")

    assert llm.complete(PAYLOAD, client=client) == "Gateway question."
    assert not beta_calls
    assert len(client.calls) == 1


def test_legacy_agentrouter_uses_claude_code_wire_image(monkeypatch):
    stable_calls: list[dict] = []
    beta_calls: list[dict] = []

    class StableMessages:
        def create(self, **kwargs):
            stable_calls.append(kwargs)
            raise AssertionError("legacy AgentRouter must use its beta URL")

    class BetaMessages:
        def create(self, **kwargs):
            beta_calls.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Gateway question.")],
                stop_reason="end_turn",
            )

    client = SimpleNamespace(
        messages=StableMessages(),
        beta=SimpleNamespace(messages=BetaMessages()),
        _eternityx_legacy_agentrouter=True,
    )
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://agentrouter.org")

    assert llm.complete(PAYLOAD, client=client) == "Gateway question."
    assert not stable_calls
    assert len(beta_calls) == 1
    call = beta_calls[0]
    assert "fallbacks" not in call and "betas" not in call
    assert call["extra_headers"]["x-app"] == "cli"
    assert call["extra_headers"]["User-Agent"].startswith("claude-cli/")
    assert call["system"][0]["text"].startswith("x-anthropic-billing-header:")
    assert call["system"][1]["text"].startswith("You are a Claude agent")
    assert call["messages"][0]["content"] == [
        {"type": "text", "text": "Go."}
    ]


@pytest.mark.parametrize(
    "reply",
    [
        "<!doctype html><title>Access Verification</title>",
        '<html><meta name="aliyun_waf_aa" content="challenge"></html>',
    ],
)
def test_html_gateway_challenge_is_not_returned_as_a_question(reply):
    with pytest.raises(llm.LLMError, match="HTML access challenge"):
        llm.complete(PAYLOAD, client=RawStringClient(reply))


# --------------------------------------------------------------------------
# End-to-end: gateway ignores the schema
# --------------------------------------------------------------------------


def test_fenced_reply_parses_without_a_retry():
    client = ScriptedClient(f"```json\n{json.dumps(VALID)}\n```")
    assert llm.complete_json(PAYLOAD, SCHEMA, client=client) == VALID
    assert len(client.calls) == 1, "a recoverable reply must not cost a second call"


def test_prose_reply_is_repaired_on_a_second_attempt():
    client = ScriptedClient("The candidate did well overall.", json.dumps(VALID))

    assert llm.complete_json(PAYLOAD, SCHEMA, client=client) == VALID
    assert len(client.calls) == 2

    # The retry has to show the model its own reply, or it repeats the prose.
    retry_messages = client.calls[1]["messages"]
    assert retry_messages[-2] == {
        "role": "assistant",
        "content": "The candidate did well overall.",
    }
    assert "not valid JSON" in retry_messages[-1]["content"]


def test_persistent_prose_raises_rather_than_returning_junk():
    client = ScriptedClient("Still prose.", "Yet more prose.", "And again.")

    with pytest.raises(llm.LLMError, match="did not return JSON"):
        llm.complete_json(PAYLOAD, SCHEMA, client=client)

    assert len(client.calls) == llm.JSON_REPAIR_ATTEMPTS + 1, "retries must be bounded"


# --------------------------------------------------------------------------
# Failures that must not be retried into
# --------------------------------------------------------------------------


def test_refusal_is_not_retried():
    client = ScriptedClient("", stop_reason="refusal")
    with pytest.raises(llm.LLMRefusal):
        llm.complete_json(PAYLOAD, SCHEMA, client=client)
    assert len(client.calls) == 1


def test_truncation_is_reported_as_truncation():
    client = ScriptedClient('{"summary": "cut off', stop_reason="max_tokens")
    with pytest.raises(llm.LLMError, match="max_tokens"):
        llm.complete_json(PAYLOAD, SCHEMA, client=client)


def test_empty_reply_is_reported_as_empty():
    client = ScriptedClient("")
    with pytest.raises(llm.LLMError, match="no text"):
        llm.complete_json(PAYLOAD, SCHEMA, client=client)


# --------------------------------------------------------------------------
# House style: no em dashes in anything the candidate reads
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # The shapes the model actually produced in live runs.
        ("Good instinct — recall before prompting.", "Good instinct, recall before prompting."),
        ("That's a real bug—good catch.", "That's a real bug, good catch."),
        # A comma is already in play, so a third one would splice two questions
        # together. The real shape this came from, verbatim off a live run.
        ("Why does that matter, though — what breaks about cosine similarity?",
         "Why does that matter, though: what breaks about cosine similarity?"),
        # The comma belongs to an earlier sentence, so this clause is still clean.
        ("Right, I see. Good instinct — recall first.",
         "Right, I see. Good instinct, recall first."),
        # Number ranges must stay ranges, not become comma splices.
        ("Days 7—10 cover embeddings.", "Days 7-10 cover embeddings."),
        ("Range 21–24.", "Range 21-24."),
        # A dash with no clause in front of it has no comma to become.
        ("— then what?", "then what?"),
        ("Right. — What next?", "Right. What next?"),
        # Nothing to do.
        ("A plain sentence with no dashes.", "A plain sentence with no dashes."),
        ("Hyphenated words like top-k stay intact.", "Hyphenated words like top-k stay intact."),
    ],
)
def test_dashes_become_ordinary_punctuation(raw, expected):
    assert llm.plain_punctuation(raw) == expected


def test_no_dash_survives_any_rewrite():
    messy = "One — two–three―four, — five. Days 7—10."
    assert not re.search(r"[—–―]", llm.plain_punctuation(messy))


def test_paragraph_breaks_are_preserved():
    """Collapsing newlines would run a multi-paragraph summary together."""
    out = llm.plain_punctuation("First para — here.\n\nSecond para — there.")
    assert out == "First para, here.\n\nSecond para, there."


def test_questions_are_cleaned_on_the_way_out():
    client = ScriptedClient("Nice — so what breaks at scale?")
    assert llm.complete(PAYLOAD, client=client) == "Nice, so what breaks at scale?"


def test_feedback_strings_are_cleaned_including_inside_lists():
    payload = {
        "summary": "Strong on retrieval — weak on agents.",
        "strengths": ["Named ChromaDB — with metadata filters."],
    }
    client = ScriptedClient(json.dumps(payload))
    result = llm.complete_json(PAYLOAD, SCHEMA, client=client)

    assert result["summary"] == "Strong on retrieval, weak on agents."
    assert result["strengths"] == ["Named ChromaDB, with metadata filters."]


def test_interviewer_and_reviewer_prompts_both_forbid_dashes():
    from app import feedback_engine, prompts

    assert "em dash" in prompts.SYSTEM_PROMPT
    assert "em dash" in feedback_engine.FEEDBACK_SYSTEM_PROMPT


# --------------------------------------------------------------------------
# Thinking blocks
# --------------------------------------------------------------------------


def test_thinking_blocks_are_ignored_in_both_paths():
    """claude-opus-5 emits adaptive thinking; only text blocks are the answer."""

    class Thinking:
        messages = beta = None

        def create(self, **kwargs):
            return SimpleNamespace(
                content=[
                    SimpleNamespace(type="thinking", thinking="deliberating"),
                    SimpleNamespace(type="text", text=json.dumps(VALID)),
                ],
                stop_reason="end_turn",
            )

    client = Thinking()
    client.messages = client
    client.beta = SimpleNamespace(messages=client)

    assert llm.complete_json(PAYLOAD, SCHEMA, client=client) == VALID
    assert llm.complete(PAYLOAD, client=client) == json.dumps(VALID)
