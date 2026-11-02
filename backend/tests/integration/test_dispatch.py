"""dispatch_next and pacing (plan 8.3, 8.10, tests 14.4 and 14.4.1)."""

import asyncio
import itertools
import uuid

from sqlalchemy import func, select

from app.models.outbound import OutboundJob
from app.services import dispatch, pacing, presence
from app.services.vendors import CreatedVendor
from tests.integration.conftest import Api, MakeApi


async def ready_vendor(api: Api, **fields: object) -> CreatedVendor:
    created = await api.vendor(**fields)
    await api.ob.online(created.vendor)
    return created


async def post_id(api: Api, key: str, **kw: object) -> str:
    response = await api.ob.post(key, **kw)  # type: ignore[arg-type]
    assert response.status_code == 202, response.text
    return str(response.json()["jobId"])


async def test_offline_plugin_keeps_the_job_queued(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    job_id = await post_id(api, created.secrets.live_key)
    assert (await api.ob.dispatch(created.vendor))["real"] == dispatch.OFFLINE
    assert await api.ob.status(job_id) == "queued"
    assert api.ob.emitter.sent == []


async def test_whatsapp_not_connected_is_treated_like_offline(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    for state in ("connecting", "offline", "logged_out", "not_loaded"):
        await api.app.state.redis.flushdb()
        await api.ob.online(created.vendor, wa_state=state)
        job_id = await post_id(api, created.secrets.live_key)
        assert (await api.ob.dispatch(created.vendor))["real"] == dispatch.OFFLINE
        assert await api.ob.status(job_id) == "queued"


async def test_online_plugin_gets_the_job(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    job_id = await post_id(
        api,
        created.secrets.live_key,
        number="03486102017",
        message="Aap ka OTP 482913 hai.",
        type="otp",
        idem="o1",
    )
    assert (await api.ob.dispatch(created.vendor))["real"] == dispatch.DISPATCHED

    row = await api.ob.row(job_id)
    assert (row.status, row.attempts) == ("dispatched", 1)
    assert row.lease_expires_at is not None
    (payload,) = api.ob.emitter.jobs_for(created.vendor.id)
    assert payload["jobId"] == job_id
    assert payload["attempt"] == 1
    assert payload["to"] == "923486102017"
    assert payload["message"] == "Aap ka OTP 482913 hai."
    assert payload["type"] == "otp"
    assert payload["expiresAt"].endswith("Z")
    assert set(payload) == {"jobId", "attempt", "to", "message", "type", "expiresAt"}
    leases = [c for c in api.ob.scheduler.calls if c.name == "check_lease"]
    assert [(c.args, c.delay) for c in leases] == [((job_id, 1), 30)]


async def test_priority_decides_the_order_then_age(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    key = created.secrets.live_key
    m1 = await post_id(api, key, type="marketing")
    m2 = await post_id(api, key, type="marketing")
    t1 = await post_id(api, key, type="transactional")
    otp = await post_id(api, key, type="otp", idem="o1")
    order = []
    for _ in range(4):
        await api.ob.dispatch(created.vendor)
        job = api.ob.emitter.jobs_for(created.vendor.id)[-1]
        order.append(job["jobId"])
        await api.ob.result(created.vendor, job["jobId"], 1, "sent", wa_ack=1)
        api.ob.clock.advance(30)  # let every pacing gap pass
    assert order == [otp, t1, m1, m2]


async def test_one_job_in_flight_per_vendor(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    key = created.secrets.live_key
    for _ in range(3):
        await post_id(api, key)
    first = await api.ob.dispatch(created.vendor)
    second = await api.ob.dispatch(created.vendor)
    assert first["real"] == dispatch.DISPATCHED
    assert second["real"] in (dispatch.BUSY, dispatch.PACED)
    assert len(api.ob.emitter.jobs_for(created.vendor.id)) == 1
    async with api.app.state.session_factory() as db:
        in_flight = await db.scalar(
            select(func.count()).select_from(OutboundJob).where(OutboundJob.status == "dispatched")
        )
    assert in_flight == 1


async def test_fifty_workers_at_once_dispatch_exactly_one_job(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    for _ in range(5):
        await post_id(api, created.secrets.live_key)
    results = await asyncio.gather(*(api.ob.dispatch(created.vendor) for _ in range(50)))
    assert sum(r["real"] == dispatch.DISPATCHED for r in results) == 1
    assert len(api.ob.emitter.jobs_for(created.vendor.id)) == 1


async def test_running_dispatch_twice_is_harmless(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    await post_id(api, created.secrets.live_key)
    await api.ob.dispatch(created.vendor)
    await api.ob.dispatch(created.vendor)
    assert len(api.ob.emitter.jobs_for(created.vendor.id)) == 1


async def test_after_a_result_otp_waits_one_to_two_seconds(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    key = created.secrets.live_key
    first = await post_id(api, key, type="otp", idem="a")
    second = await post_id(api, key, type="otp", idem="b")
    await api.ob.dispatch(created.vendor)
    await api.ob.result(created.vendor, first, 1, "sent", wa_ack=1)

    fast = float(await api.app.state.redis.get(pacing.pace_key(created.vendor.id, "fast")))
    slow = float(await api.app.state.redis.get(pacing.pace_key(created.vendor.id, "slow")))
    now = api.ob.clock.now
    assert 1.0 <= fast - now <= 2.0
    assert 8.0 <= slow - now <= 20.0

    assert (await api.ob.dispatch(created.vendor))["real"] == dispatch.PACED
    assert await api.ob.status(second) == "queued"
    api.ob.clock.advance(2.01)
    assert (await api.ob.dispatch(created.vendor))["real"] == dispatch.DISPATCHED


async def test_marketing_gap_is_at_least_eight_seconds(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    key = created.secrets.live_key
    ids = [await post_id(api, key, type="marketing") for _ in range(4)]
    sent_at: list[float] = []
    for expected in ids:
        for _ in range(200):  # move time forward in small steps until the job goes
            if (await api.ob.dispatch(created.vendor))["real"] == dispatch.DISPATCHED:
                break
            api.ob.clock.advance(0.5)
        job = api.ob.emitter.jobs_for(created.vendor.id)[-1]
        assert job["jobId"] == expected
        sent_at.append(api.ob.clock.now)
        await api.ob.result(created.vendor, expected, 1, "sent", wa_ack=1)
    gaps = [b - a for a, b in itertools.pairwise(sent_at)]
    assert all(8.0 <= g <= 20.5 for g in gaps), gaps


async def test_otp_does_not_wait_for_the_marketing_gap(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    key = created.secrets.live_key
    m1 = await post_id(api, key, type="marketing")
    m2 = await post_id(api, key, type="marketing")
    await api.ob.dispatch(created.vendor)
    await api.ob.result(created.vendor, m1, 1, "sent", wa_ack=1)

    # m2 must wait 8 to 20 seconds, but an OTP arriving now only waits the short gap.
    assert (await api.ob.dispatch(created.vendor))["real"] == dispatch.PACED
    otp = await post_id(api, key, type="otp", idem="otp-now")
    api.ob.clock.advance(2.01)
    assert (await api.ob.dispatch(created.vendor))["real"] == dispatch.DISPATCHED
    assert api.ob.emitter.jobs_for(created.vendor.id)[-1]["jobId"] == otp
    assert await api.ob.status(m2) == "queued"


async def test_one_wake_alarm_for_a_hundred_waiting_jobs(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    key = created.secrets.live_key
    first = await post_id(api, key, type="marketing")
    await api.ob.dispatch(created.vendor)
    await api.ob.result(created.vendor, first, 1, "sent", wa_ack=1)
    for _ in range(100):
        await post_id(api, key, type="marketing")
    api.ob.scheduler.calls.clear()
    for _ in range(100):
        await api.ob.dispatch(created.vendor)
    alarms = [c for c in api.ob.scheduler.calls if c.name == "dispatch" and c.delay > 0]
    assert len(alarms) == 1
    assert 0 < alarms[0].delay <= 20


async def test_an_alarm_that_already_rang_does_not_block_a_new_one(make_api: MakeApi) -> None:
    redis = (await make_api()).app.state.redis
    vendor = uuid.uuid4()
    assert await pacing.claim_wake_slot(redis, vendor, 5, now=1000.0)
    assert not await pacing.claim_wake_slot(redis, vendor, 5, now=1001.0)  # covered
    assert await pacing.claim_wake_slot(redis, vendor, 1, now=1001.0)  # earlier alarm is needed
    assert await pacing.claim_wake_slot(redis, vendor, 5, now=1010.0)  # the old one rang long ago


async def test_wiped_pace_keys_do_not_lose_jobs(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    key = created.secrets.live_key
    first = await post_id(api, key, type="marketing")
    second = await post_id(api, key, type="marketing")
    await api.ob.dispatch(created.vendor)
    await api.ob.result(created.vendor, first, 1, "sent", wa_ack=1)
    await api.app.state.redis.delete(
        pacing.pace_key(created.vendor.id, "fast"), pacing.pace_key(created.vendor.id, "slow")
    )
    assert (await api.ob.dispatch(created.vendor))["real"] == dispatch.DISPATCHED
    assert api.ob.emitter.jobs_for(created.vendor.id)[-1]["jobId"] == second


async def test_one_vendors_wait_does_not_hold_back_another(make_api: MakeApi) -> None:
    api = await make_api()
    a = await ready_vendor(api)
    b = await ready_vendor(api)
    first = await post_id(api, a.secrets.live_key, type="marketing")
    await post_id(api, a.secrets.live_key, type="marketing")
    await api.ob.dispatch(a.vendor)
    await api.ob.result(a.vendor, first, 1, "sent", wa_ack=1)
    waiting = await api.ob.dispatch(a.vendor)
    assert waiting["real"] == dispatch.PACED

    b_job = await post_id(api, b.secrets.live_key, type="marketing")
    assert (await api.ob.dispatch(b.vendor))["real"] == dispatch.DISPATCHED
    assert api.ob.emitter.jobs_for(b.vendor.id)[0]["jobId"] == b_job


async def test_jobs_only_go_to_their_own_vendor(make_api: MakeApi) -> None:
    api = await make_api()
    vendors = [await ready_vendor(api) for _ in range(5)]
    expected: dict[str, set[str]] = {}
    for vendor in vendors:
        ids = {await post_id(api, vendor.secrets.live_key) for _ in range(1)}
        expected[str(vendor.vendor.id)] = ids
    for vendor in vendors:
        await api.ob.dispatch(vendor.vendor)
    for vendor_id, payload in api.ob.emitter.sent:
        assert payload["jobId"] in expected[vendor_id]
    assert len(api.ob.emitter.sent) == 5


async def test_paused_outbound_holds_jobs_back(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api, outbound_mode="paused")
    job_id = await post_id(api, created.secrets.live_key)
    assert (await api.ob.dispatch(created.vendor))["real"] == dispatch.PAUSED
    assert await api.ob.status(job_id) == "queued"


async def test_suspended_vendor_gets_nothing(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    job_id = await post_id(api, created.secrets.live_key)
    async with api.app.state.session_factory() as db:
        vendor = await db.get(type(created.vendor), created.vendor.id)
        assert vendor is not None
        vendor.status = "suspended"
        await db.commit()
    assert (await api.ob.dispatch(created.vendor))["real"] == dispatch.SUSPENDED
    assert await api.ob.status(job_id) == "queued"


async def test_expired_job_is_never_sent(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    job_id = await post_id(api, created.secrets.live_key, type="otp", ttl=60, idem="o1")
    api.ob.clock.advance(3600)  # nobody came for an hour
    await api.ob.dispatch(created.vendor)
    assert await api.ob.status(job_id) == "expired"
    assert api.ob.emitter.sent == []


async def test_a_failing_hub_leaves_a_recoverable_job(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    job_id = await post_id(api, created.secrets.live_key)
    api.ob.emitter.fail = True
    assert (await api.ob.dispatch(created.vendor))["real"] == dispatch.DISPATCHED
    assert await api.ob.status(job_id) == "dispatched"  # the lease sweeper will take it back


async def test_plugin_going_offline_mid_queue(make_api: MakeApi) -> None:
    api = await make_api()
    created = await ready_vendor(api)
    job_id = await post_id(api, created.secrets.live_key)
    await presence.release(api.app.state.redis, created.vendor.id, "x")  # wrong sid: no effect
    assert await presence.is_online(api.app.state.redis, created.vendor.id)
    await api.app.state.redis.delete(presence.presence_key(created.vendor.id))
    assert (await api.ob.dispatch(created.vendor))["real"] == dispatch.OFFLINE
    assert await api.ob.status(job_id) == "queued"
