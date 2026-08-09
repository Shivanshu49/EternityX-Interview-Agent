"""Prompt assembly tests, focused on interviewer calibration.

The cohort runs from an intern with no professional experience to a
distinguished engineer with 28 years, and several candidates came from outside
engineering entirely. Asking all of them the same question at the same depth is
the most obvious way this agent could feel canned, so the calibration is pinned
down here rather than left to the model's judgement.
"""

from __future__ import annotations

import pytest

from app import prompts
from app.models import Candidate, CohortSignals, Member, Mission


def candidate(role: str | None = None, years: int | None = None, **kw) -> Candidate:
    return Candidate(
        member=Member(name="Test Person", job_role=role, years_experience=years, **kw),
        missions=[Mission(day=7, passed=True, attempts=1)],
        signals=CohortSignals(commitDays=20, missionsCompleted=10, missionsFirstTry=5),
    )


# --------------------------------------------------------------------------
# Depth
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role, years",
    [
        ("Distinguished Engineer", 28),
        ("Principal Architect", 8),      # title outranks a mid-range year count
        ("Senior Data Engineer", 15),
        ("Legacy Systems Engineer", 25),
    ],
)
def test_senior_candidates_are_not_asked_for_definitions(role, years):
    text = prompts.render_calibration(candidate(role, years))
    assert "senior engineer" in text
    assert "Skip definitions" in text


@pytest.mark.parametrize(
    "role, years",
    [
        ("Computer Science Intern", 0),
        ("Junior Developer", 1),
        ("Software Engineer", 2),
    ],
)
def test_early_career_candidates_are_asked_about_what_they_built(role, years):
    text = prompts.render_calibration(candidate(role, years))
    assert "early-career" in text
    assert "not had yet" in text


def test_a_junior_title_wins_over_a_high_year_count():
    """A mislabelled record should not get a principal's interview."""
    text = prompts.render_calibration(candidate("Junior Developer", 20))
    assert "early-career" in text


def test_mid_level_gets_reasoning_not_definitions_or_scale():
    text = prompts.render_calibration(candidate("Software Engineer", 5))
    assert "mid-level" in text
    assert "senior engineer" not in text and "early-career" not in text


# --------------------------------------------------------------------------
# Register
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role", ["Marketing Manager", "HR Manager", "Business Analyst", "UX Researcher"]
)
def test_non_engineering_roles_drop_the_insider_shorthand(role):
    text = prompts.render_calibration(candidate(role, 6))
    assert "outside the field" in text
    assert "same standard" in text, "judged equally, just not in jargon"


@pytest.mark.parametrize(
    "role",
    [
        "Senior Data Engineer", "Mobile App Developer", "DevOps Engineer",
        "Principal Architect", "Computer Science Intern", "IT Support Specialist",
    ],
)
def test_technical_roles_keep_the_engineering_register(role):
    assert "outside the field" not in prompts.render_calibration(candidate(role, 5))


def test_absent_role_is_not_treated_as_evidence_of_anything():
    """No role on the record must not be read as 'not an engineer'."""
    text = prompts.render_calibration(candidate())
    assert "outside the field" not in text
    assert "mid-level" in text


# --------------------------------------------------------------------------
# The profile the model actually receives
# --------------------------------------------------------------------------


def test_profile_states_role_and_experience_and_the_calibration():
    text = prompts.render_candidate_profile(candidate("Distinguished Engineer", 28))
    assert "a Distinguished Engineer with 28 years of experience" in text
    assert "Skip definitions" in text


def test_profile_handles_a_candidate_with_no_experience_yet():
    text = prompts.render_candidate_profile(candidate("Computer Science Intern", 0))
    assert "no professional experience yet" in text
    assert "0 years" not in text


def test_profile_says_one_year_not_one_years():
    assert "1 year of experience" in prompts.render_candidate_profile(
        candidate("Junior Developer", 1)
    )


def test_profile_omits_the_clause_when_there_is_no_role():
    text = prompts.render_candidate_profile(candidate())
    assert "Test Person, who just completed" in text


def test_profile_forbids_reciting_the_role_back_at_the_candidate():
    text = prompts.render_candidate_profile(candidate("HR Manager", 6))
    assert "never mention their job title" in text


def test_camelcase_json_populates_the_calibration_fields():
    """Candidate payloads arrive from the API as camelCase."""
    parsed = Candidate.model_validate(
        {"member": {"name": "X", "jobRole": "Distinguished Engineer",
                    "yearsExperience": 28}, "missions": [], "signals": {}}
    )
    assert parsed.member.job_role == "Distinguished Engineer"
    assert parsed.member.years_experience == 28
    assert "Skip definitions" in prompts.render_calibration(parsed)
