"""LLM client wrapper. Owner: A.

The only module that talks to the model. Everything else builds a payload and
hands it here, so the engine stays testable: pass any object exposing
`messages.create(...)` as `client` and no network call happens.

Speaks the Anthropic Messages API, which means it also works against a gateway
that implements it -- set ANTHROPIC_BASE_URL and the SDK routes there. That is
how this project runs on an AgentRouter key rather than a first-party Anthropic
one. See `complete_json` for the one place the two are not interchangeable.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

import anthropic
from dotenv import load_dotenv

# Read .env at import time so a key placed there actually reaches the SDK.
# Without this the file was decorative: python-dotenv is a declared dependency
# and .env.example ships, but nothing ever loaded it, so putting a key in .env
# failed silently with an authentication error. override=False means a real
# exported environment variable always wins over the file.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

# Adaptive thinking is on by default on this tier, so we never pass a `thinking`
# param -- we just leave room for it in `max_tokens`, which caps thinking and
# response text together.
#
# The default is deliberately a model the configured endpoint actually serves.
# An unknown name does not fail at startup, it fails on the first question with
# a 503 from the gateway ("no available channel for model X"), so overriding
# LLM_MODEL is only safe against a provider you know carries that model.
DEFAULT_MODEL = os.getenv("LLM_MODEL", "claude-opus-5")

# `medium`, not `low`. Sonnet 5 honours effort strictly at the low end: at `low`
# it scopes its work tightly to what was asked, which is right for lookups and
# wrong for this call -- choosing a follow-up means reading the candidate's last
# answer and reacting to it, which is judgment work. Sonnet 5 at `medium` lands
# around where Sonnet 4.6 sat at `high`, so latency stays interactive.
DEFAULT_EFFORT = "medium"

# Headroom for thinking, not for the answer. The visible output is two or three
# sentences; the budget exists so adaptive thinking cannot crowd the question out
# and trip the max_tokens guard below. Unused budget costs nothing.
DEFAULT_MAX_TOKENS = 4000

# Sonnet 5 is far more cyber-capable than Sonnet 4.6 and ships the matching
# safeguards, so a security-flavoured curriculum day can draw a refusal (HTTP 200
# with stop_reason "refusal"). `fallbacks: "default"` re-runs the request
# server-side on Anthropic's recommended model instead of handing us the refusal.
# Set False to drop the beta dependency -- the refusal guard in `complete` stays
# either way.
ENABLE_REFUSAL_FALLBACK = True
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Flipped off permanently the first time the installed SDK or the API rejects
# the fallback parameter, so we retry the beta path at most once per process.
_fallback_available = True


def _uses_first_party_anthropic() -> bool:
    """Whether Anthropic-only beta features are safe to send.

    Messages-compatible gateways implement the stable endpoint, but they do
    not necessarily implement Anthropic's beta query route or ``fallbacks``
    parameter. Sending those fields to AgentRouter currently trips its WAF and
    returns a CAPTCHA page with HTTP 200, so beta fallback is reserved for the
    first-party API.
    """
    endpoint = os.getenv("ANTHROPIC_BASE_URL", "").strip().rstrip("/").lower()
    return not endpoint or endpoint == "https://api.anthropic.com"


# The end-of-interview report is one call at the end of a session, so latency
# matters far less than it does per question -- more room to reason, more room
# to write four sections of prose.
FEEDBACK_EFFORT = "medium"
FEEDBACK_MAX_TOKENS = 6000


# Everything the candidate reads passes through this module, so the house style
# is enforced here rather than trusted to the prompt. Models reach for em dashes
# constantly and an instruction not to only mostly works; one that slips into a
# question is the kind of tell that makes a live interview read as generated.
# Number ranges keep a hyphen ("days 7-10"); elsewhere a dash is doing the job of
# a comma, so it becomes one.
_NUMERIC_RANGE = re.compile(r"(?<=\d)\s*[—–―]\s*(?=\d)")
_DASH_AFTER_WORD = re.compile(r"(?<=[\w)\]\"'`*])\s*[—–―]\s*")
_DASH_ANY = re.compile(r"\s*[—–―]\s*")


def _dash_replacement(match: re.Match[str]) -> str:
    """A comma, or a colon where a comma would make a third one in one clause.

    "Good instinct - recall first" wants a comma. But the model also writes
    "Why does that matter, though - what breaks?", and a comma there produces a
    splice joining two questions. A period would be worse: it would make two
    questions out of a turn the prompt requires to contain exactly one.
    """
    clause = re.split(r"[.!?]", match.string[: match.start()])[-1]
    return ": " if "," in clause else ", "


def plain_punctuation(text: str) -> str:
    """Rewrite em and en dashes as ordinary punctuation.

    Deliberately not a general prettifier: it fixes the one habit that reads as
    machine-written and leaves everything else the model wrote alone.
    """
    text = _NUMERIC_RANGE.sub("-", text)
    text = _DASH_AFTER_WORD.sub(_dash_replacement, text)
    # A dash that followed punctuation or opened a line has no comma to become.
    text = _DASH_ANY.sub(" ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"([.!?;:])\s*,\s*", r"\1 ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    # Spaces and tabs only: collapsing newlines would run paragraphs together.
    text = re.sub(r"[ \t]{2,}", " ", text)
    # A dash that opened a line leaves the space that followed it behind. This
    # text is always prose, never indented content, so a leading space is noise.
    return re.sub(r"^[ \t]+", "", text, flags=re.MULTILINE)


def _clean_strings(value: Any) -> Any:
    """Apply `plain_punctuation` to every string inside a parsed JSON value."""
    if isinstance(value, str):
        return plain_punctuation(value)
    if isinstance(value, list):
        return [_clean_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean_strings(item) for key, item in value.items()}
    return value


def _response_text(message: Any) -> str:
    """Normalize Anthropic SDK and gateway-compatible response shapes.

    The official SDK returns a Message containing typed content blocks. Some
    compatible gateways return the generated text directly, and others expose
    ``content`` as a plain string. All three represent the same logical result.
    """
    if isinstance(message, str):
        return message.strip()

    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if not content:
        return ""

    return "".join(
        block.text for block in content if getattr(block, "type", None) == "text"
    ).strip()


def _reject_non_model_response(text: str) -> str:
    """Reject HTML error/challenge pages returned with a successful status."""
    prefix = text.lstrip()[:512].lower()
    if (
        prefix.startswith("<!doctype html")
        or prefix.startswith("<html")
        or "aliyun_waf" in prefix
    ):
        raise LLMError("Model gateway returned an HTML access challenge.")
    return text


class LLMError(RuntimeError):
    """The model returned nothing usable."""


class LLMRefusal(LLMError):
    """Safety classifiers declined the request."""


class LLMConfigurationError(LLMError):
    """No usable Anthropic credential. A deployment problem, not a model one."""


def describe_endpoint() -> str:
    """Which host requests will actually go to. Reported at startup.

    The SDK reads ANTHROPIC_BASE_URL itself; this only surfaces the resulting
    value so a running server never talks to an endpoint nobody expected
    without saying so in the log.
    """
    return os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com (default)"


_MISSING_CREDENTIAL_HINT = (
    "No API credential found. Set ANTHROPIC_API_KEY in .env or in the "
    "environment the server runs in, then restart. When the key belongs to a "
    "gateway rather than to Anthropic, ANTHROPIC_BASE_URL must be set too, or "
    "the key is sent to api.anthropic.com and rejected."
)


def _is_missing_credential(exc: Exception) -> bool:
    """The SDK signals absent auth as a TypeError at request time, not at init."""
    message = str(exc).lower()
    return "authentication" in message or "api_key" in message


class SupportsMessages(Protocol):
    """The slice of `anthropic.Anthropic` this module actually uses."""

    messages: Any


_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    """Lazily build the default client.

    Lazy so that importing the engine never requires an API key -- tests and the
    sanity-check script inject their own stub instead.
    """
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _create(client: SupportsMessages, **kwargs: Any) -> Any:
    """Call the Messages API, preferring the refusal-fallback beta."""
    global _fallback_available

    if ENABLE_REFUSAL_FALLBACK and _fallback_available and _uses_first_party_anthropic():
        try:
            return client.beta.messages.create(  # type: ignore[attr-defined]
                betas=[_FALLBACK_BETA], fallbacks="default", **kwargs
            )
        except (TypeError, AttributeError, anthropic.BadRequestError) as exc:
            # A missing credential also surfaces as TypeError here. Treating it
            # as "the beta is unsupported" would disable refusal fallbacks for
            # the life of the process over an unrelated deployment problem.
            if _is_missing_credential(exc):
                raise LLMConfigurationError(_MISSING_CREDENTIAL_HINT) from exc
            # SDK too old to type `fallbacks`, or the beta is unavailable on this
            # key. Degrade to the plain endpoint and stop trying.
            _fallback_available = False

    try:
        return client.messages.create(**kwargs)
    except TypeError as exc:
        if _is_missing_credential(exc):
            raise LLMConfigurationError(_MISSING_CREDENTIAL_HINT) from exc
        raise


def complete(
    payload: dict[str, Any],
    *,
    client: SupportsMessages | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str = DEFAULT_EFFORT,
) -> str:
    """Run one non-streaming completion and return its text.

    `payload` is `{"system": ..., "messages": [...]}` as built by
    `prompts.build_message_payload`.
    """
    message = _create(
        client or get_client(),
        model=model,
        max_tokens=max_tokens,
        output_config={"effort": effort},
        **payload,
    )

    # Check the stop reason before touching `content` -- on a refusal it is
    # empty, and on a truncation it is a half-written question.
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        raise LLMRefusal(f"Request declined by safety classifiers ({model}).")

    text = _reject_non_model_response(_response_text(message))

    if not text:
        raise LLMError(f"Model returned no text (stop_reason={stop_reason!r}).")
    if stop_reason == "max_tokens":
        raise LLMError(
            "Response hit max_tokens and is truncated -- raise DEFAULT_MAX_TOKENS."
        )
    return plain_punctuation(text)


# Structured outputs are a request, not a guarantee, once a gateway is in the
# path. api.anthropic.com enforces `output_config.format` server-side; a
# Messages-API-compatible proxy typically accepts the field and drops it, so the
# model never hears about the schema and answers in prose. That failure is
# silent and lands at the worst possible moment -- the end-of-interview report,
# the one response a candidate actually reads -- so the schema is also stated in
# the prompt and the reply is parsed defensively. Against Anthropic directly the
# instruction is simply redundant.
_JSON_DIRECTIVE = """\
Return a single JSON object and nothing else. No markdown code fences, no \
commentary before or after it. It must validate against this JSON Schema, \
whose field descriptions are instructions to you:

{schema}"""

# One corrective attempt when the reply is not parseable JSON. Cheap insurance:
# this runs once per interview, and the alternative is a 502 in place of the
# report.
JSON_REPAIR_ATTEMPTS = 1

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _with_json_directive(
    payload: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any]:
    """Append the schema to the system prompt, for endpoints that ignore it."""
    directive = _JSON_DIRECTIVE.format(schema=json.dumps(schema, indent=2))
    system = payload.get("system")
    return {
        **payload,
        "system": f"{system}\n\n{directive}" if system else directive,
    }


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object out of a reply that may be fenced or padded with prose.

    Raises `LLMError` if nothing parseable is found, so the caller can decide
    whether to ask again.
    """
    candidates = [text, _FENCE.sub("", text).strip()]

    # Last resort: the outermost braces. Slicing rather than scanning is enough
    # here because the target is one object, so any prose the model wrapped it
    # in sits entirely outside the first `{` and the last `}`.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise LLMError("Reply contained no JSON object.")


def complete_json(
    payload: dict[str, Any],
    schema: dict[str, Any],
    *,
    client: SupportsMessages | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = FEEDBACK_MAX_TOKENS,
    effort: str = FEEDBACK_EFFORT,
) -> dict[str, Any]:
    """Run one completion constrained to `schema` and return the parsed object.

    Pass `SomeModel.model_json_schema()` -- pydantic emits the
    `additionalProperties` and `required` keys the API needs.

    The schema goes out twice: as `output_config.format`, which only Anthropic
    itself enforces, and restated in the prompt, which every endpoint honours.
    The reply is then parsed with `_extract_json_object` rather than `json.loads`
    and retried once if that fails, so a provider that drops the format field
    degrades to a slightly less reliable path instead of a broken one.
    """
    request = _with_json_directive(payload, schema)
    messages = list(request["messages"])
    last_error: LLMError | None = None

    for attempt in range(JSON_REPAIR_ATTEMPTS + 1):
        message = _create(
            client or get_client(),
            model=model,
            max_tokens=max_tokens,
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            system=request["system"],
            messages=messages,
        )

        stop_reason = getattr(message, "stop_reason", None)
        if stop_reason == "refusal":
            raise LLMRefusal(f"Request declined by safety classifiers ({model}).")
        if stop_reason == "max_tokens":
            raise LLMError(
                "Structured response hit max_tokens and is truncated -- raise "
                "FEEDBACK_MAX_TOKENS."
            )

        text = _reject_non_model_response(_response_text(message))
        if not text:
            raise LLMError(f"Model returned no text (stop_reason={stop_reason!r}).")

        try:
            return _clean_strings(_extract_json_object(text))
        except LLMError as exc:
            last_error = exc
            if attempt == JSON_REPAIR_ATTEMPTS:
                break
            # Show the model its own reply and name the fault. A bare retry
            # tends to reproduce the same prose.
            messages = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        "That was not valid JSON. Send the same content again as "
                        "a single JSON object matching the schema, with no code "
                        "fences and no text around it."
                    ),
                },
            ]

    raise LLMError(
        f"Model did not return JSON after {JSON_REPAIR_ATTEMPTS + 1} attempts."
    ) from last_error
