"""A real Celery worker process, the real broker (Redis) and a real Socket.IO socket.

The other outbound tests drive the same service functions in-process with fakes. This file shows
the pieces also work when they are separate processes talking through Redis: API -> Celery ->
worker -> Redis -> hub -> plugin, and back (plan 8.3, 14.4).
"""

import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest

from app.services.scheduling import CeleryScheduler
from app.worker.factory import make_celery
from tests.integration.live_support import Live


@pytest.fixture
async def worker(live: Live) -> AsyncIterator[None]:
    api = live.api
    settings = api.settings
    env = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": settings.database_url,
        "REDIS_URL": settings.redis_url,
        "ALLOW_PRIVATE_BOT_URLS": "true",
        "LOG_LEVEL": "WARNING",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "celery",
        "-A",
        "app.worker.celery_app",
        "worker",
        "-Q",
        "dispatch,maintenance,webhooks",
        "--pool=solo",
        "-l",
        "INFO",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None
    try:
        async with asyncio.timeout(60):
            while True:
                line = await process.stdout.readline()
                if not line:
                    raise AssertionError("celery worker exited before it was ready")
                if b" ready." in line:
                    break
        # From here on the API hands its work to the real broker, and the clock is real time.
        api.ob.rt = replace(
            api.ob.rt, scheduler=CeleryScheduler(make_celery(settings)), clock=time.time
        )
        api.app.state.runtime = api.ob.rt
        yield
    finally:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 15)
        except TimeoutError:
            process.kill()


async def wait_for_status(live: Live, job_id: str, wanted: str, seconds: float = 30) -> None:
    async with asyncio.timeout(seconds):
        while True:
            if await live.api.ob.status(job_id) == wanted:
                return
            await asyncio.sleep(0.2)


async def test_test_key_job_is_finished_by_the_worker(live: Live, worker: None) -> None:
    created = await live.api.vendor()
    response = await live.api.ob.post(created.secrets.test_key)
    assert response.status_code == 202
    await wait_for_status(live, response.json()["jobId"], "sent")


async def test_real_job_goes_worker_to_redis_to_socket_and_back(live: Live, worker: None) -> None:
    api = live.api
    created = await api.vendor()
    plugin = await live.connect(created)
    assert (await plugin.heartbeat("connected"))["ok"] is True

    response = await api.ob.post(created.secrets.live_key, message="hello over redis")
    job_id = response.json()["jobId"]
    await plugin.wait_for_jobs(1, timeout_seconds=30)  # the worker emitted it, not this process
    assert plugin.jobs[0]["jobId"] == job_id
    assert plugin.jobs[0]["message"] == "hello over redis"

    assert (await plugin.ack(job_id, 1))["ok"] is True
    assert (await plugin.result(job_id, 1, "sent", waAck=1))["ok"] is True
    assert await api.ob.status(job_id) == "sent"


async def test_second_job_follows_after_the_pacing_gap(live: Live, worker: None) -> None:
    """The worker wakes itself with a delayed task after the 1-2 second gap: no sleeping loop."""
    api = live.api
    created = await api.vendor()
    plugin = await live.connect(created)
    await plugin.heartbeat("connected")
    ids = [(await api.ob.post(created.secrets.live_key)).json()["jobId"] for _ in range(2)]
    await plugin.wait_for_jobs(1, timeout_seconds=30)
    first = plugin.jobs[0]
    await plugin.ack(first["jobId"], 1)
    started = time.monotonic()
    await plugin.result(first["jobId"], 1, "sent", waAck=1)
    await plugin.wait_for_jobs(2, timeout_seconds=30)
    assert 0.5 <= time.monotonic() - started < 10  # about the 1-2 s gap plus Celery latency
    assert [j["jobId"] for j in plugin.jobs] == ids


async def test_job_waits_in_the_queue_while_the_plugin_is_offline_then_flows(
    live: Live, worker: None
) -> None:
    api = live.api
    created = await api.vendor()
    job_id = (await api.ob.post(created.secrets.live_key)).json()["jobId"]
    await asyncio.sleep(2)
    assert await api.ob.status(job_id) == "queued"
    plugin = await live.connect(created)  # connecting wakes dispatch
    await plugin.heartbeat("connected")
    await plugin.wait_for_jobs(1, timeout_seconds=30)
    assert plugin.jobs[0]["jobId"] == job_id


async def test_killing_the_worker_mid_job_loses_nothing(live: Live) -> None:
    """acks_late: the message goes back to the broker if the worker dies before finishing.
    Here the broker simply keeps the message while no worker runs, and a new one takes it."""
    api = live.api
    created = await api.vendor()
    plugin = await live.connect(created)
    await plugin.heartbeat("connected")
    api.ob.rt = replace(
        api.ob.rt, scheduler=CeleryScheduler(make_celery(api.settings)), clock=time.time
    )
    api.app.state.runtime = api.ob.rt
    job_id = (await api.ob.post(created.secrets.live_key)).json()["jobId"]  # no worker yet
    await asyncio.sleep(1)
    assert await api.ob.status(job_id) == "queued" and plugin.jobs == []
    env = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": api.settings.database_url,
        "REDIS_URL": api.settings.redis_url,
        "LOG_LEVEL": "WARNING",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "celery",
        "-A",
        "app.worker.celery_app",
        "worker",
        "-Q",
        "dispatch,maintenance,webhooks",
        "--pool=solo",
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await plugin.wait_for_jobs(1, timeout_seconds=60)
    finally:
        process.terminate()
        await process.wait()
    assert plugin.jobs[0]["jobId"] == job_id
