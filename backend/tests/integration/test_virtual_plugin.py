"""The virtual plugin behind TEST keys: the magic numbers vendors use to try every outcome."""

import pytest

from app.services import sweepers
from tests.integration.conftest import Api, MakeApi


async def run_test_job(api: Api, number: str) -> tuple[str, str]:
    created = await api.vendor()
    job_id = (await api.ob.post(created.secrets.test_key, number=number)).json()["jobId"]
    await api.ob.dispatch(created.vendor)
    await api.ob.drain(delayed=True)
    return job_id, await api.ob.status(job_id)


async def test_ordinary_number_is_sent(make_api: MakeApi) -> None:
    api = await make_api()
    job_id, status = await run_test_job(api, "03001234567")
    row = await api.ob.row(job_id)
    assert status == "sent" and row.wa_ack == 1 and row.attempts == 1


async def test_number_ending_0001_is_not_on_whatsapp(make_api: MakeApi) -> None:
    api = await make_api()
    job_id, status = await run_test_job(api, "03000000001")
    assert status == "failed"
    assert (await api.ob.row(job_id)).error_code == "NOT_ON_WHATSAPP"


async def test_number_ending_0002_is_not_ready_once_then_sent(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    job_id = (await api.ob.post(created.secrets.test_key, number="03000000002")).json()["jobId"]
    await api.ob.dispatch(created.vendor)
    await api.ob.drain()
    assert await api.ob.status(job_id) == "queued"  # first attempt: WA_NOT_READY, back to the queue
    api.ob.clock.advance(30)
    await api.ob.dispatch(created.vendor)
    await api.ob.drain(delayed=True)
    row = await api.ob.row(job_id)
    assert (row.status, row.attempts) == ("sent", 2)


async def test_number_ending_0003_never_answers_and_becomes_unknown(make_api: MakeApi) -> None:
    api = await make_api()
    job_id, status = await run_test_job(api, "03000000003")
    assert status == "sending"
    api.ob.clock.advance(121)
    async with api.app.state.session_factory() as db:
        await sweepers.sweep_leases(db, api.ob.rt, now=api.ob.clock.dt)
    assert await api.ob.status(job_id) == "unknown"


async def test_number_ending_0004_takes_thirty_seconds(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    job_id = (await api.ob.post(created.secrets.test_key, number="03000000004")).json()["jobId"]
    await api.ob.dispatch(created.vendor)
    await api.ob.drain()
    assert await api.ob.status(job_id) == "sending"
    finish = next(c for c in api.ob.scheduler.calls if c.name == "run_virtual")
    assert finish.delay == pytest.approx(30.0)
    await api.ob.drain(delayed=True)
    assert await api.ob.status(job_id) == "sent"


async def test_test_traffic_never_reaches_a_real_plugin(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    await api.ob.online(created.vendor)
    await api.ob.post(created.secrets.test_key)
    await api.ob.dispatch(created.vendor)
    await api.ob.drain(delayed=True)
    assert api.ob.emitter.jobs_for(created.vendor.id) == []


async def test_test_job_works_while_the_real_plugin_is_busy(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    await api.ob.online(created.vendor)
    live = (await api.ob.post(created.secrets.live_key)).json()["jobId"]
    test = (await api.ob.post(created.secrets.test_key)).json()["jobId"]
    await api.ob.dispatch(created.vendor)
    await api.ob.drain(delayed=True)
    assert await api.ob.status(live) == "dispatched"
    assert await api.ob.status(test) == "sent"


async def test_test_jobs_are_not_paced_and_skip_the_daily_cap(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(marketing_daily_cap=1)
    for _ in range(3):
        response = await api.ob.post(created.secrets.test_key, type="marketing")
        assert response.status_code == 202
