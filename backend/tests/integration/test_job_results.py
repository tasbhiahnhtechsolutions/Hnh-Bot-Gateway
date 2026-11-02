"""What happens when the plugin reports started / sent / failed (plan 8.6, 8.7, 14.4)."""

import pytest

from app.services import sweepers
from app.services.job_results import Outcome
from app.services.job_rules import MAX_ATTEMPTS
from app.services.vendors import CreatedVendor
from tests.integration.conftest import Api, MakeApi


async def dispatched(
    api: Api, *, job_type: str = "transactional", **fields: object
) -> tuple[CreatedVendor, str]:
    """A vendor whose plugin is online, with one job handed to it. Returns (vendor, job id)."""
    created = await api.vendor(**fields)
    await api.ob.online(created.vendor)
    response = await api.ob.post(
        created.secrets.live_key, type=job_type, idem="k-1" if job_type == "otp" else None
    )
    job_id = str(response.json()["jobId"])
    await api.ob.dispatch(created.vendor)
    assert await api.ob.status(job_id) == "dispatched"
    return created, job_id


async def test_started_then_sent(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api)
    started = await api.ob.started(created.vendor, job_id, 1)
    assert started.outcome is Outcome.APPLIED
    row = await api.ob.row(job_id)
    assert row.status == "sending"
    assert row.lease_expires_at is not None

    sent = await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    assert sent.outcome is Outcome.APPLIED
    row = await api.ob.row(job_id)
    assert (row.status, row.wa_ack, row.error_code) == ("sent", 1, None)
    assert row.sent_at is not None and row.finished_at is not None and row.lease_expires_at is None


async def test_duplicate_reports_change_nothing(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api)
    await api.ob.started(created.vendor, job_id, 1)
    assert (await api.ob.started(created.vendor, job_id, 1)).outcome is Outcome.DUPLICATE
    first = await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    again = [await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=2) for _ in range(3)]
    assert first.outcome is Outcome.APPLIED
    assert all(r.outcome is Outcome.DUPLICATE for r in again)
    assert (await api.ob.row(job_id)).wa_ack == 1  # the first answer stands


async def test_result_before_started_is_accepted_out_of_order(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api)
    assert (
        await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    ).outcome is Outcome.APPLIED
    late = await api.ob.started(created.vendor, job_id, 1)  # arrives after the result
    assert late.outcome is Outcome.IGNORED
    assert await api.ob.status(job_id) == "sent"


async def test_another_vendors_job_is_refused(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api)
    intruder = await api.vendor()
    assert (await api.ob.started(intruder.vendor, job_id, 1)).outcome is Outcome.NOT_FOUND
    refused = await api.ob.result(intruder.vendor, job_id, 1, "sent", wa_ack=1)
    assert refused.outcome is Outcome.NOT_FOUND
    assert await api.ob.status(job_id) == "dispatched"  # untouched
    unknown = await api.ob.result(created.vendor, "job_" + "0" * 16, 1, "sent")
    assert unknown.outcome is Outcome.NOT_FOUND


async def test_not_on_whatsapp_fails_for_good(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api, job_type="transactional")
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "failed", error_code="NOT_ON_WHATSAPP")
    row = await api.ob.row(job_id)
    assert (row.status, row.error_code) == ("failed", "NOT_ON_WHATSAPP")


async def test_unknown_error_codes_are_shown_as_send_failed(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api, job_type="otp")
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "failed", error_code="SOMETHING_ODD")
    row = await api.ob.row(job_id)
    assert (row.status, row.error_code) == ("failed", "SEND_FAILED")


async def test_otp_is_never_retried_after_a_send_failure(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api, job_type="otp")
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "failed", error_code="SEND_FAILED")
    assert await api.ob.status(job_id) == "failed"


async def test_transactional_send_failure_goes_back_to_the_queue(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api, job_type="transactional")
    await api.ob.started(created.vendor, job_id, 1)
    applied = await api.ob.result(created.vendor, job_id, 1, "failed", error_code="SEND_FAILED")
    assert applied.outcome is Outcome.REQUEUED
    row = await api.ob.row(job_id)
    assert row.status == "queued"
    assert row.available_at > api.ob.clock.dt  # a short pause before the next try
    assert row.error_code == "SEND_FAILED"
    api.ob.clock.advance(30)
    await api.ob.dispatch(created.vendor)
    assert (await api.ob.row(job_id)).attempts == 2


async def test_retries_stop_after_the_attempt_limit(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(
        api,
        job_type="transactional",
    )
    for attempt in range(1, MAX_ATTEMPTS + 1):
        await api.ob.started(created.vendor, job_id, attempt)
        await api.ob.result(created.vendor, job_id, attempt, "failed", error_code="SEND_FAILED")
        if attempt < MAX_ATTEMPTS:
            assert await api.ob.status(job_id) == "queued"
            api.ob.clock.advance(120)
            await api.ob.dispatch(created.vendor)
    row = await api.ob.row(job_id)
    assert (row.status, row.attempts, row.error_code) == ("failed", MAX_ATTEMPTS, "SEND_FAILED")


@pytest.mark.parametrize("job_type", ["otp", "transactional", "marketing"])
async def test_wa_not_ready_before_started_is_safe_to_retry_for_every_type(
    make_api: MakeApi, job_type: str
) -> None:
    api = await make_api()
    created, job_id = await dispatched(api, job_type=job_type)
    applied = await api.ob.result(created.vendor, job_id, 1, "failed", error_code="WA_NOT_READY")
    assert applied.outcome is Outcome.REQUEUED  # nothing was sent, even an OTP may go again
    assert await api.ob.status(job_id) == "queued"


async def test_wa_not_ready_after_started_is_not_retried(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api, job_type="otp")
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "failed", error_code="WA_NOT_READY")
    assert await api.ob.status(job_id) == "failed"


async def test_an_old_attempts_failure_cannot_kill_the_current_one(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api, job_type="transactional")
    await api.ob.result(created.vendor, job_id, 1, "failed", error_code="WA_NOT_READY")
    api.ob.clock.advance(60)
    await api.ob.dispatch(created.vendor)
    assert (await api.ob.row(job_id)).attempts == 2
    stale = await api.ob.result(created.vendor, job_id, 1, "failed", error_code="NOT_ON_WHATSAPP")
    assert stale.outcome is Outcome.IGNORED
    assert await api.ob.status(job_id) == "dispatched"


async def test_a_late_sent_report_is_always_believed(make_api: MakeApi) -> None:
    """The plugin did send the OTP, but its "started" got lost and the lease took the job back.
    The truth wins, so nobody sends the OTP a second time."""
    api = await make_api()
    created, job_id = await dispatched(api, job_type="otp")
    api.ob.clock.advance(31)
    async with api.app.state.session_factory() as db:
        assert (
            await sweepers.check_lease(db, api.ob.rt, job_id, 1, now=api.ob.clock.dt) == "requeued"
        )
    assert await api.ob.status(job_id) == "queued"
    late = await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    assert late.outcome is Outcome.APPLIED
    assert await api.ob.status(job_id) == "sent"
    await api.ob.dispatch(created.vendor)
    assert api.ob.emitter.jobs_for(created.vendor.id)[-1]["attempt"] == 1  # never dispatched again


async def test_a_late_sent_report_corrects_unknown(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api, job_type="otp")
    await api.ob.started(created.vendor, job_id, 1)
    api.ob.clock.advance(200)
    async with api.app.state.session_factory() as db:
        await sweepers.sweep_leases(db, api.ob.rt, now=api.ob.clock.dt)
    assert await api.ob.status(job_id) == "unknown"
    await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    assert await api.ob.status(job_id) == "sent"


async def test_failed_job_is_not_revived_by_a_sent_report(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api, job_type="otp")
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "failed", error_code="SEND_FAILED")
    ignored = await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    assert ignored.outcome is Outcome.IGNORED
    assert await api.ob.status(job_id) == "failed"


async def test_plugin_can_report_that_the_ttl_ran_out_before_starting(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api, job_type="otp")
    await api.ob.result(created.vendor, job_id, 1, "expired")
    assert await api.ob.status(job_id) == "expired"


async def test_finishing_a_job_starts_the_pacing_gap_and_wakes_dispatch(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await dispatched(api)
    api.ob.scheduler.calls.clear()
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    assert api.ob.scheduler.names() == ["dispatch"]
    assert await api.app.state.redis.exists(f"pace:vendor:{created.vendor.id}:fast")


async def test_test_jobs_do_not_start_pacing(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    job_id = (await api.ob.post(created.secrets.test_key)).json()["jobId"]
    await api.ob.dispatch(created.vendor)
    await api.ob.drain()
    await api.ob.drain(delayed=True)
    assert await api.ob.status(job_id) == "sent"
    assert not await api.app.state.redis.exists(f"pace:vendor:{created.vendor.id}:fast")
