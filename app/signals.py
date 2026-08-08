"""Turn a candidate's cohort record into interview strategy.

This layer is deliberately deterministic -- no LLM. The engine's opening beliefs
about a candidate come from arithmetic on their actual cohort behaviour, so the
same record always produces the same interview plan and the reasoning is
inspectable.

One mission per day means one classification per day: `classify()` takes a
`Mission` directly. There is no aggregation step and no intermediate signal type
to translate through.
"""

from __future__ import annotations

from app.models import (
    Candidate,
    Curriculum,
    DayProfile,
    Mission,
    QuestionKind,
    SignalPattern,
)

# At or above this many attempts, a pass reads as ground out rather than known.
GRINDER_ATTEMPT_THRESHOLD = 3

# Opening belief per pattern: (mastery, uncertainty).
#
# Uncertainty is the interesting number -- it decides what gets asked. GRINDER
# and AVOIDED score highest because those are the two patterns where the record
# genuinely does not tell us whether the candidate understands the material:
# grinding to a pass looks identical from the outside whether the candidate
# finally understood it or finally guessed it, and a skip says nothing about
# whether they couldn't or just didn't.
_PRIORS: dict[SignalPattern, tuple[float, float]] = {
    SignalPattern.FLUENT: (0.85, 0.25),
    SignalPattern.STEADY: (0.65, 0.35),
    SignalPattern.GRINDER: (0.40, 0.75),
    SignalPattern.STRUGGLED: (0.20, 0.45),
    SignalPattern.AVOIDED: (0.15, 0.80),
    SignalPattern.UNKNOWN: (0.50, 0.90),
}

# How to open on a day, given the pattern. The point of the whole engine:
# a fluent candidate and an avoidant one get different interviews.
_OPENING_MOVE: dict[SignalPattern, QuestionKind] = {
    SignalPattern.FLUENT: QuestionKind.EDGE_CASE,
    SignalPattern.STEADY: QuestionKind.APPLY,
    SignalPattern.GRINDER: QuestionKind.PROBE_DEPTH,
    SignalPattern.STRUGGLED: QuestionKind.DIAGNOSE_GAP,
    SignalPattern.AVOIDED: QuestionKind.SCAFFOLD,
    SignalPattern.UNKNOWN: QuestionKind.APPLY,
}


def classify(mission: Mission | None) -> SignalPattern:
    """Classify the candidate's behaviour on a single day.

    A missing row and a never-started mission are both UNKNOWN rather than
    STRUGGLED -- zero attempts is absence of evidence, not evidence of a gap.
    """
    if mission is None or not mission.has_record:
        return SignalPattern.UNKNOWN
    if mission.skipped:
        return SignalPattern.AVOIDED
    if not mission.passed:
        return SignalPattern.STRUGGLED
    if mission.attempts <= 1:
        return SignalPattern.FLUENT
    if mission.attempts >= GRINDER_ATTEMPT_THRESHOLD:
        return SignalPattern.GRINDER
    return SignalPattern.STEADY


def evidence(pattern: SignalPattern, mission: Mission | None) -> str:
    """Plain-language justification for a belief, for the report's audit trail."""
    attempts = mission.attempts if mission else 0
    plural = "" if attempts == 1 else "s"

    match pattern:
        case SignalPattern.FLUENT:
            return "Passed on the first try."
        case SignalPattern.STEADY:
            return f"Passed in {attempts} attempt{plural}."
        case SignalPattern.GRINDER:
            return (
                f"Needed {attempts} attempts to pass -- reached the answer, but the "
                "route there suggests the understanding may be shallow."
            )
        case SignalPattern.STRUGGLED:
            return f"Made {attempts} attempt{plural} without passing."
        case SignalPattern.AVOIDED:
            return "Skipped without attempting it."
        case _:
            return "No cohort data for this day."


def opening_move(pattern: SignalPattern) -> QuestionKind:
    """The interview move to open with for a given signal pattern."""
    return _OPENING_MOVE[pattern]


def build_profiles(
    candidate: Candidate, curriculum: Curriculum | None = None
) -> dict[int, DayProfile]:
    """Build one belief per day the candidate has a record for, keyed by day."""
    profiles: dict[int, DayProfile] = {}

    for mission in candidate.missions:
        pattern = classify(mission)
        mastery, uncertainty = _PRIORS[pattern]
        curriculum_day = curriculum.get(mission.day) if curriculum else None

        profiles[mission.day] = DayProfile(
            day=mission.day,
            title=(curriculum_day.title if curriculum_day else mission.title),
            pattern=pattern,
            mastery=mastery,
            uncertainty=uncertainty,
            evidence=evidence(pattern, mission),
            attempts=mission.attempts,
            passed=mission.passed,
            skipped=mission.skipped,
        )
    return profiles


def update_belief(profile: DayProfile, score: float, weight: float = 0.6) -> DayProfile:
    """Fold an answer's score into the belief about a day.

    A weighted move toward the observed score, with uncertainty shrinking each
    time we get a direct observation. Deliberately simple and monotonic: the
    interview should converge, and a reviewer should be able to follow how.
    """
    posterior_mastery = (1 - weight) * profile.mastery + weight * score
    posterior_uncertainty = profile.uncertainty * (1 - weight)
    return profile.model_copy(
        update={
            "mastery": round(min(1.0, max(0.0, posterior_mastery)), 3),
            "uncertainty": round(min(1.0, max(0.0, posterior_uncertainty)), 3),
        }
    )
