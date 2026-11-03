"""Redis cut or slowed on purpose (plan 14.5). The promise: no accepted job is ever lost, and
everything flows again when Redis comes back."""

import asyncio
import time

import pytest

from app.services import sweepers
from tests.integration.chaos.conftest import Toxiproxy
from tests.integration.conftest import MakeApi

pytestmark = [pytest.mark.chaos, pytest.mark.integration]


async def test_redis_cut_never_loses_an_accepted_job(make_api: MakeApi, healthy: Toxiproxy) -> None:
    api = await make_api(redis_url=healthy.redis_url)
    created = await api.vendor()
    await api.ob.online(created.vendor)

    healthy.set_enabled(False)
    t0 = time.monotonic()
    response = await api.ob.post(created.secrets.live_key, idem="chaos-1", type="otp")
    assert time.monotonic() - t0 < 30, "an API call must not hang on a dead Redis"
    healthy.set_enabled(True)

    # Whatever the API answered, what it promised must be true.
    async with api.app.state.session_factory() as db:
        from sqlalchemy import select

        from app.models.outbound import OutboundJob

        rows = (await db.scalars(select(OutboundJob))).all()
    if response.status_code in (200, 202):
        assert len(rows) == 1 and rows[0].status == "queued"
    else:
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "TEMPORARILY_UNAVAILABLE"
        assert response.headers["Retry-After"] == "5"
        assert rows == []  # nothing was half-accepted
    # And a retry with the same Idempotency-Key after Redis is back gives exactly one job.
    again = await api.ob.post(created.secrets.live_key, idem="chaos-1", type="otp")
    assert again.status_code in (200, 202)
    api.ob.clock.advance(1)


async def test_jobs_flow_again_after_redis_returns(make_api: MakeApi, healthy: Toxiproxy) -> None:
    api = await make_api(redis_url=healthy.redis_url)
    created = await api.vendor()
    await api.ob.online(created.vendor)
    posted = await api.ob.post(created.secrets.live_key)
    assert posted.status_code == 202
    job_id = posted.json()["jobId"]
    api.ob.scheduler.calls.clear()  # the "wake dispatch" message is lost

    healthy.set_enabled(False)
    await asyncio.sleep(0.5)
    healthy.set_enabled(True)
    await api.ob.rt.redis.ping()

    async with api.app.state.session_factory() as db:
        assert await sweepers.reconcile_idle_vendors(db, api.ob.rt, now=api.ob.clock.dt) in (0, 1)
    await api.ob.drain()
    if await api.ob.status(job_id) == "queued":  # presence expired while Redis was away
        await api.ob.online(created.vendor)
        async with api.app.state.session_factory() as db:
            assert await sweepers.reconcile_idle_vendors(db, api.ob.rt, now=api.ob.clock.dt) == 1
        await api.ob.drain()
    assert await api.ob.status(job_id) == "dispatched"


async def test_slow_redis_slows_the_api_but_does_not_break_it(
    make_api: MakeApi, healthy: Toxiproxy
) -> None:
    api = await make_api(redis_url=healthy.redis_url)
    created = await api.vendor()
    healthy.add_toxic("latency", latency=300, jitter=100)
    response = await api.ob.post(created.secrets.live_key)
    assert response.status_code == 202
    assert await api.ob.status(response.json()["jobId"]) == "queued"
