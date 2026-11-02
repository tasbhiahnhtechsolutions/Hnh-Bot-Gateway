import pytest

from app.models.outbound import JobStatus, JobType
from app.services.job_rules import (
    ALLOWED_TRANSITIONS,
    FAST_GAP,
    FINAL_STATUSES,
    SLOW_GAP,
    TTL_MAX,
    TTL_MIN,
    TYPE_RULES,
    WEBHOOK_EVENTS,
    WEBHOOK_MAX_ATTEMPTS,
    WEBHOOK_RETRY_DELAYS,
    can_transition,
    clamp_ttl,
)


def test_type_table_matches_the_plan() -> None:
    otp, transactional, marketing = (TYPE_RULES[t.value] for t in JobType)
    assert (otp.priority, transactional.priority, marketing.priority) == (9, 5, 0)
    assert (otp.default_ttl, transactional.default_ttl, marketing.default_ttl) == (300, 3600, 86400)
    assert (otp.lane, transactional.lane, marketing.lane) == ("fast", "fast", "slow")
    assert not otp.auto_retry  # a second OTP must never go out by itself
    assert transactional.auto_retry and marketing.auto_retry
    assert marketing.counts_for_daily_cap
    assert not otp.counts_for_daily_cap and not transactional.counts_for_daily_cap


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (None, 300),
        (5, TTL_MIN),
        (30, 30),
        (300, 300),
        (100_000, TTL_MAX),
        (-5, TTL_MIN),
        (0, TTL_MIN),
    ],
)
def test_ttl_is_clamped_and_defaults_per_type(given: int | None, expected: int) -> None:
    assert clamp_ttl(given, "otp") == expected


def test_default_ttl_depends_on_the_type() -> None:
    assert clamp_ttl(None, "transactional") == 3600
    assert clamp_ttl(None, "marketing") == 86400


def test_gaps_follow_the_plan() -> None:
    assert FAST_GAP == (1.0, 2.0)
    assert SLOW_GAP == (8.0, 20.0)


def test_every_status_has_a_row_and_final_ones_do_not_move_back() -> None:
    assert set(ALLOWED_TRANSITIONS) == {s.value for s in JobStatus}
    for final in (JobStatus.SENT, JobStatus.FAILED, JobStatus.EXPIRED, JobStatus.CANCELLED):
        assert ALLOWED_TRANSITIONS[final.value] == frozenset()
    # ``unknown`` may only be corrected by an honest late report, never re-queued.
    assert ALLOWED_TRANSITIONS[JobStatus.UNKNOWN.value] == {"sent", "failed"}
    assert {
        s.value
        for s in JobStatus
        if s not in (JobStatus.QUEUED, JobStatus.DISPATCHED, JobStatus.SENDING)
    } == FINAL_STATUSES


@pytest.mark.parametrize(
    ("old", "new", "ok"),
    [
        ("queued", "dispatched", True),
        ("dispatched", "sending", True),
        ("dispatched", "queued", True),  # lease ran out
        ("sending", "sent", True),
        ("sending", "unknown", True),
        ("sending", "queued", True),  # a retryable failure of a non-OTP
        ("queued", "sending", False),  # nobody can start a job that was never handed out
        ("queued", "failed", False),
        ("sent", "queued", False),
        ("sent", "failed", False),
        ("expired", "dispatched", False),  # an expired job is never dispatched
        ("cancelled", "queued", False),
        ("unknown", "queued", False),  # OTP safety: never retried automatically
        ("nonsense", "sent", False),
    ],
)
def test_transition_table(old: str, new: str, ok: bool) -> None:
    assert can_transition(old, new) is ok


def test_webhook_events_and_retry_schedule() -> None:
    assert WEBHOOK_EVENTS == {
        "sent": "message.sent",
        "failed": "message.failed",
        "expired": "message.expired",
        "unknown": "message.unknown",
    }
    assert WEBHOOK_RETRY_DELAYS == (60, 300, 1800, 7200, 21600)
    assert WEBHOOK_MAX_ATTEMPTS == 6
