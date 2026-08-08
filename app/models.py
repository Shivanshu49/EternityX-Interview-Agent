"""Shared Pydantic schemas for the interview API and engine boundaries."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InterviewRequest(BaseModel):
    """Request accepted by the single interview endpoint."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: str = Field(alias="sessionId", min_length=1)
    candidate: dict[str, Any] | None = None
    message: str | None = None

    @field_validator("session_id", "message")
    @classmethod
    def reject_blank_strings(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class QuestionResult(BaseModel):
    """Contract returned by the question engine."""

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1)
    day: int = Field(ge=1, le=31)


class Feedback(BaseModel):
    """Required structured feedback format."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    strengths: list[str]
    gaps: list[str]
    next: list[str]


class InterviewResponse(BaseModel):
    """Successful response from the interview endpoint."""

    reply: str
    done: bool
    feedback: Feedback | None = None
