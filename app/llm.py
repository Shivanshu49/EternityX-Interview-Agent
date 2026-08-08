"""LLM client wrapper. Owner: A.

The only module that talks to Anthropic. Everything else builds a payload and
hands it here, so the engine stays testable: pass any object exposing
`messages.create(...)` as `client` and no network call happens.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import anthropic

# Claude Sonnet 5. Adaptive thinking is on by default here (as on the Opus
# tier), so we never pass a `thinking` param -- we just leave room for it in
# `max_tokens`, which caps thinking and response text together.
DEFAULT_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5")

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


class LLMError(RuntimeError):
    """The model returned nothing usable."""


class LLMRefusal(LLMError):
    """Safety classifiers declined the request."""


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

    if ENABLE_REFUSAL_FALLBACK and _fallback_available:
        try:
            return client.beta.messages.create(  # type: ignore[attr-defined]
                betas=[_FALLBACK_BETA], fallbacks="default", **kwargs
            )
        except (TypeError, AttributeError, anthropic.BadRequestError):
            # SDK too old to type `fallbacks`, or the beta is unavailable on this
            # key. Degrade to the plain endpoint and stop trying.
            _fallback_available = False

    return client.messages.create(**kwargs)


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

    text = "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    ).strip()

    if not text:
        raise LLMError(f"Model returned no text (stop_reason={stop_reason!r}).")
    if stop_reason == "max_tokens":
        raise LLMError(
            "Response hit max_tokens and is truncated -- raise DEFAULT_MAX_TOKENS."
        )
    return text
