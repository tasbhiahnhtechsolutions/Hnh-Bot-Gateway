"""Rules of an outbound job: types, TTL, the state machine (plan sections 8.1.1, 8.6, 14.1)."""

from dataclasses import dataclass

from app.models.outbound import JobStatus, JobType

TTL_MIN = 30
TTL_MAX = 86_400
MAX_MESSAGE_CHARS = 4000
MAX_ATTEMPTS = 5  # how often a job may go back to the queue before it is given up
REQUEUE_DELAY_SECONDS = 10  # WhatsApp was not ready: look again a little later

LEASE_STARTED_SECONDS = 30  # plugin must say "started" within this time after dispatch
LEASE_RESULT_SECONDS = 120  # and send the result within this time after "started"
WA_ACK_WAIT_SECONDS = 15  # plugin side: how long it waits for a WhatsApp ack
BODY_RETENTION_HOURS = 24


@dataclass(frozen=True, slots=True)
class TypeRules:
    priority: int
    default_ttl: int
    lane: str  # "fast" or "slow" pacing key
    auto_retry: bool  # may a failed job go back to the queue?
    counts_for_daily_cap: bool


TYPE_RULES: dict[str, TypeRules] = {
    JobType.OTP.value: TypeRules(9, 300, "fast", auto_retry=False, counts_for_daily_cap=False),
    JobType.TRANSACTIONAL.value: TypeRules(
        5, 3600, "fast", auto_retry=True, counts_for_daily_cap=False
    ),
    JobType.MARKETING.value: TypeRules(
        0, 86_400, "slow", auto_retry=True, counts_for_daily_cap=True
    ),
}

# Gap after a finished job, in seconds (low, high). Marketing waits much longer.
FAST_GAP = (1.0, 2.0)
SLOW_GAP = (8.0, 20.0)


def clamp_ttl(ttl_seconds: int | None, job_type: str) -> int:
    if ttl_seconds is None:
        return TYPE_RULES[job_type].default_ttl
    return max(TTL_MIN, min(TTL_MAX, ttl_seconds))


S = JobStatus

# Where a job may go from where it is. Everything else is a bug and is refused.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    S.QUEUED.value: frozenset(
        {
            S.DISPATCHED.value,
            S.EXPIRED.value,
            S.CANCELLED.value,
            S.SENT.value,  # the plugin did send it, only its "started" got lost (late truth)
        }
    ),
    S.DISPATCHED.value: frozenset(
        {
            S.SENDING.value,
            S.QUEUED.value,  # lease ran out, or WhatsApp not ready: nothing was sent
            S.SENT.value,  # result arrived before the "started" ack (out of order)
            S.FAILED.value,
            S.EXPIRED.value,  # plugin noticed the TTL passed before it started
            S.CANCELLED.value,
        }
    ),
    S.SENDING.value: frozenset(
        {S.SENT.value, S.FAILED.value, S.UNKNOWN.value, S.QUEUED.value, S.CANCELLED.value}
    ),
    S.UNKNOWN.value: frozenset({S.SENT.value, S.FAILED.value}),  # a late honest report
    S.SENT.value: frozenset(),
    S.FAILED.value: frozenset(),
    S.EXPIRED.value: frozenset(),
    S.CANCELLED.value: frozenset(),
}

FINAL_STATUSES = frozenset(
    {S.SENT.value, S.FAILED.value, S.EXPIRED.value, S.CANCELLED.value, S.UNKNOWN.value}
)
IN_FLIGHT_STATUSES = frozenset({S.DISPATCHED.value, S.SENDING.value})

# Final statuses that vendors are told about by webhook.
WEBHOOK_EVENTS: dict[str, str] = {
    S.SENT.value: "message.sent",
    S.FAILED.value: "message.failed",
    S.EXPIRED.value: "message.expired",
    S.UNKNOWN.value: "message.unknown",
}

# Plugin error codes. The vendor only ever sees NOT_ON_WHATSAPP or SEND_FAILED (plan 8.8).
ERROR_NOT_ON_WHATSAPP = "NOT_ON_WHATSAPP"
ERROR_SEND_FAILED = "SEND_FAILED"
ERROR_WA_NOT_READY = "WA_NOT_READY"  # nothing was sent: safe to try again, even for an OTP
PUBLIC_ERROR_CODES = frozenset({ERROR_NOT_ON_WHATSAPP, ERROR_SEND_FAILED})


def can_transition(old: str, new: str) -> bool:
    return new in ALLOWED_TRANSITIONS.get(old, frozenset())


# Pause before webhook retry number 1, 2, 3, ... (seconds): 1 min, 5 min, 30 min, 2 h, 6 h.
WEBHOOK_RETRY_DELAYS = (60, 300, 1800, 7200, 21_600)
WEBHOOK_MAX_ATTEMPTS = len(WEBHOOK_RETRY_DELAYS) + 1
