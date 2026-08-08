"""HTTP API surface for stateful technical interviews."""

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

from app import question_engine, report
from app.models import Feedback, InterviewRequest, InterviewResponse, QuestionResult
from app.session_store import (
    can_finish,
    create_session,
    delete_session,
    get_session,
    update_session,
)


router = APIRouter()


def _next_question(session: dict[str, Any]) -> QuestionResult:
    try:
        raw_result = question_engine.next_question(deepcopy(session))
        return QuestionResult.model_validate(raw_result)
    except (ValidationError, TypeError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Question engine returned an invalid result.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Question engine is temporarily unavailable.",
        ) from exc


def _generate_feedback(session: dict[str, Any]) -> Feedback:
    try:
        raw_feedback = report.generate(deepcopy(session))
        return Feedback.model_validate(raw_feedback)
    except (ValidationError, TypeError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Feedback engine returned an invalid result.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feedback engine is temporarily unavailable.",
        ) from exc


def _record_question(session: dict[str, Any], question: QuestionResult) -> None:
    session["history"].append(
        {"role": "interviewer", "content": question.reply, "day": question.day}
    )
    session["days_covered"].append(question.day)
    session["questions_asked"] += 1


@router.post(
    "/api/interview",
    response_model=InterviewResponse,
    response_model_exclude_none=True,
)
def interview(request: InterviewRequest) -> InterviewResponse:
    """Start an interview, process one answer, or return final feedback."""

    is_start = request.candidate is not None and request.message is None
    is_turn = request.message is not None and request.candidate is None

    if not is_start and not is_turn:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either candidate (start) or message (turn), but not both.",
        )

    if is_start:
        if not request.candidate:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Candidate must be a non-empty object.",
            )
        if get_session(request.session_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A session with this sessionId already exists.",
            )

        session = create_session(request.session_id, request.candidate)
        try:
            question = _next_question(session)
        except HTTPException:
            delete_session(request.session_id)
            raise
        _record_question(session, question)
        return InterviewResponse(reply=question.reply, done=False)

    session = get_session(request.session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Interview session not found.",
        )
    if session["phase"] == "done":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Interview session is already complete.",
        )

    # Work against a copy so an engine failure leaves the stored session safe to retry.
    working_session = deepcopy(session)
    working_session["history"].append(
        {"role": "candidate", "content": request.message}
    )

    if can_finish(working_session):
        working_session["phase"] = "wrapping_up"
        feedback = _generate_feedback(working_session)
        working_session["phase"] = "done"
        update_session(request.session_id, **working_session)
        return InterviewResponse(
            reply="Interview completed.", done=True, feedback=feedback
        )

    question = _next_question(working_session)
    _record_question(working_session, question)
    update_session(request.session_id, **working_session)
    return InterviewResponse(reply=question.reply, done=False)
