"""The safety nets: lost jobs, stuck jobs, expired jobs, wiped bodies (plan 8.5, 14.4)."""

from datetime import timedelta

from sqlalchemy import update

from app.models.outbound import OutboundJob
from app.services import sweepers
from app.services.vendors import CreatedVendor
from tests.integration.conftest import Api, MakeApi


async def one_job(api: Api, **post: object) -> tuple[CreatedVendor, str]:
    created = await api.vendor()
    await api.ob.online(created.vendor)
    response = await api.ob.post(created.secrets.live_key, **post)  # type: ignore[arg-type]
    return created, str(response.json()["jobId"])


async def sweep(api: Api) -> dict[str, int]:
    async with api.app.state.session_factory() as db:
        return await sweepers.sweep_leases(db, api.ob.rt, now=api.ob.clock.dt)


async def test_a_job_the_plugin_never_heard_of_goes_back_to_the_queue(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await one_job(api)
    await api.ob.dispatch(created.vendor)
    assert await api.ob.status(job_id) == "dispatched"
    assert await sweep(api) == {"requeued": 0, "unknown": 0}  # lease still running
    api.ob.clock.advance(31)
    assert await sweep(api) == {"requeued": 1, "unknown": 0}
    assert await api.ob.status(job_id) == "queued"
    await api.ob.dispatch(created.vendor)
    row = await api.ob.row(job_id)
    assert (row.status, row.attempts) == ("dispatched", 2)


async def test_a_started_job_without_a_result_becomes_unknown_and_is_never_resent(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    created, job_id = await one_job(api, type="otp", idem="otp-1")
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    api.ob.clock.advance(121)
    assert await sweep(api) == {"requeued": 0, "unknown": 1}
    row = await api.ob.row(job_id)
    assert (row.status, row.error_code) == ("unknown", "NO_RESULT")
    await api.ob.dispatch(created.vendor)
    assert len(api.ob.emitter.jobs_for(created.vendor.id)) == 1  # one send, never two


async def test_unknown_job_is_reported_to_the_vendor(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(status_webhook_url="http://hook.test/status")
    await api.ob.online(created.vendor)
    job_id = (await api.ob.post(created.secrets.live_key)).json()["jobId"]
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    api.ob.clock.advance(121)
    await sweep(api)
    rows = await api.ob.deliveries(created.vendor.id)
    assert [(r.event, r.payload["status"]) for r in rows] == [("message.unknown", "unknown")]


async def test_sweeper_is_idempotent(make_api: MakeApi) -> None:
    api = await make_api()
    created, _ = await one_job(api)
    await api.ob.dispatch(created.vendor)
    api.ob.clock.advance(31)
    first = await sweep(api)
    second = await sweep(api)
    assert first["requeued"] == 1 and second == {"requeued": 0, "unknown": 0}


async def test_delayed_lease_check_requeues_only_the_attempt_it_was_made_for(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    created, job_id = await one_job(api)
    await api.ob.dispatch(created.vendor)
    api.ob.clock.advance(31)
    async with api.app.state.session_factory() as db:
        assert await sweepers.check_lease(db, api.ob.rt, job_id, 2, now=api.ob.clock.dt) == "ok"
        assert (
            await sweepers.check_lease(db, api.ob.rt, job_id, 1, now=api.ob.clock.dt) == "requeued"
        )
        assert await sweepers.check_lease(db, api.ob.rt, job_id, 1, now=api.ob.clock.dt) == "ok"


async def test_lease_check_does_nothing_when_the_plugin_started_in_time(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await one_job(api)
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    api.ob.clock.advance(31)
    async with api.app.state.session_factory() as db:
        assert await sweepers.check_lease(db, api.ob.rt, job_id, 1, now=api.ob.clock.dt) == "ok"
    assert await api.ob.status(job_id) == "sending"


async def test_late_result_beats_the_sweeper(make_api: MakeApi) -> None:
    """The result and the sweeper meet on the same job at the same moment. Whoever comes first,
    the vendor ends up with ``sent`` and the OTP is not sent twice."""
    api = await make_api()
    created, job_id = await one_job(api, type="otp", idem="otp-race")
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    api.ob.clock.advance(121)

    import asyncio

    await asyncio.gather(
        sweep(api),
        api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1),
    )
    assert await api.ob.status(job_id) == "sent"
    await api.ob.dispatch(created.vendor)
    assert len(api.ob.emitter.jobs_for(created.vendor.id)) == 1


async def test_jobs_past_their_ttl_expire_and_the_vendor_is_told(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(status_webhook_url="http://hook.test/status")  # plugin offline
    job_id = (await api.ob.post(created.secrets.live_key, type="otp", ttl=30, idem="otp-x")).json()[
        "jobId"
    ]
    api.ob.clock.advance(31)
    async with api.app.state.session_factory() as db:
        assert await sweepers.expire_jobs(db, api.ob.rt, now=api.ob.clock.dt) == 1
        assert await sweepers.expire_jobs(db, api.ob.rt, now=api.ob.clock.dt) == 0
    assert await api.ob.status(job_id) == "expired"
    rows = await api.ob.deliveries(created.vendor.id)
    assert [r.event for r in rows] == ["message.expired"]


async def test_an_in_flight_job_is_not_expired_under_the_plugin(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await one_job(api, type="otp", ttl=30, idem="otp-ttl")
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    api.ob.clock.advance(40)
    async with api.app.state.session_factory() as db:
        assert await sweepers.expire_jobs(db, api.ob.rt, now=api.ob.clock.dt) == 0
    assert await api.ob.status(job_id) == "sending"


async def test_reconcile_wakes_a_vendor_whose_wake_up_message_was_lost(make_api: MakeApi) -> None:
    api = await make_api()
    created, _ = await one_job(api)
    api.ob.scheduler.calls.clear()  # the message to Celery is gone
    async with api.app.state.session_factory() as db:
        assert await sweepers.reconcile_idle_vendors(db, api.ob.rt, now=api.ob.clock.dt) == 1
    assert api.ob.scheduler.names() == ["dispatch"]
    await api.ob.drain()
    assert len(api.ob.emitter.jobs_for(created.vendor.id)) == 1


async def test_reconcile_leaves_offline_vendors_and_busy_vendors_alone(make_api: MakeApi) -> None:
    api = await make_api()
    offline = await api.vendor()
    await api.ob.post(offline.secrets.live_key)
    busy, _ = await one_job(api)
    await api.ob.dispatch(busy.vendor)
    await api.ob.post(busy.secrets.live_key)
    api.ob.scheduler.calls.clear()
    async with api.app.state.session_factory() as db:
        assert await sweepers.reconcile_idle_vendors(db, api.ob.rt, now=api.ob.clock.dt) == 0


async def test_bodies_are_wiped_a_day_after_the_final_status(make_api: MakeApi) -> None:
    api = await make_api()
    created, job_id = await one_job(api)
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    queued = (await api.ob.post(created.secrets.live_key, message="still waiting")).json()["jobId"]
    async with api.app.state.session_factory() as db:
        assert await sweepers.purge_bodies(db, now=api.ob.clock.dt + timedelta(hours=23)) == 0
        assert await sweepers.purge_bodies(db, now=api.ob.clock.dt + timedelta(hours=25)) == 1
        assert await sweepers.purge_bodies(db, now=api.ob.clock.dt + timedelta(hours=25)) == 0
    assert (await api.ob.row(job_id)).body is None
    assert (await api.ob.row(queued)).body == "still waiting"  # not final: kept


async def test_lost_webhook_messages_are_picked_up_again(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(status_webhook_url="http://hook.test/status")
    await api.ob.online(created.vendor)
    job_id = (await api.ob.post(created.secrets.live_key)).json()["jobId"]
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    api.ob.scheduler.calls.clear()  # Celery lost the delivery
    async with api.app.state.session_factory() as db:
        assert await sweepers.due_webhooks(db, api.ob.rt, now=api.ob.clock.dt) == 0  # too fresh
        assert (
            await sweepers.due_webhooks(db, api.ob.rt, now=api.ob.clock.dt + timedelta(seconds=90))
            == 1
        )
    assert api.ob.scheduler.names() == ["deliver_webhook"]


async def test_scheduler_failure_after_a_result_does_not_lose_the_result(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    created, job_id = await one_job(api)
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    api.ob.scheduler.broken = True  # the broker is down
    await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    assert await api.ob.status(job_id) == "sent"


async def test_old_jobs_with_a_dead_lease_all_come_back(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    await api.ob.online(created.vendor)
    ids = [(await api.ob.post(created.secrets.live_key)).json()["jobId"] for _ in range(3)]
    await api.ob.dispatch(created.vendor)
    async with api.app.state.session_factory() as db:  # simulate a very old lease
        await db.execute(
            update(OutboundJob)
            .where(OutboundJob.status == "dispatched")
            .values(lease_expires_at=api.ob.clock.dt - timedelta(days=1))
        )
        await db.commit()
    assert (await sweep(api))["requeued"] == 1
    assert sorted([await api.ob.status(i) for i in ids]) == ["queued"] * 3
