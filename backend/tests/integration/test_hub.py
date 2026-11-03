"""The Socket.IO line between the gateway and the plugin, over a real TCP connection
(plan 8.4, 8.9, 14.4). A real python-socketio client plays the plugin."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import select

from app.models.outbound import Device
from app.services import presence
from tests.integration.live_support import Live


async def eventually(check: Callable[[], Awaitable[bool]], timeout_seconds: float = 5.0) -> None:
    async with asyncio.timeout(timeout_seconds):
        while not await check():  # noqa: ASYNC110
            await asyncio.sleep(0.05)


async def test_valid_key_connects_and_takes_the_line(live: Live) -> None:
    created = await live.api.vendor()
    await live.connect(created)
    redis = live.api.app.state.redis
    assert await presence.holder_sid(redis, created.vendor.id) is not None
    async with live.api.app.state.session_factory() as db:
        device = await db.scalar(select(Device).where(Device.vendor_id == created.vendor.id))
    assert device is not None
    assert (device.device_id, device.plugin_version) == ("device-0001", "0.2.0")
    assert device.wa_number == created.vendor.phone_e164


@pytest.mark.parametrize(
    ("auth", "code"),
    [
        ({"apiKey": "wab_live_nope_nope"}, "INVALID_KEY"),
        ({"apiKey": 12345}, "INVALID_KEY"),
        ({"deviceId": "x"}, "INVALID_DEVICE"),
        ({"waNumber": "923009999999"}, "NUMBER_MISMATCH"),
        ({"waNumber": ""}, "NUMBER_MISMATCH"),
    ],
)
async def test_bad_credentials_are_refused_with_a_clear_code(
    live: Live, auth: dict[str, Any], code: str
) -> None:
    created = await live.api.vendor()
    assert code in await live.refused(created, **auth)
    assert await presence.holder_sid(live.api.app.state.redis, created.vendor.id) is None


async def test_test_keys_cannot_open_the_line(live: Live) -> None:
    created = await live.api.vendor()
    assert "TEST_KEY_NOT_ALLOWED" in await live.refused(created, key=created.secrets.test_key)


async def test_suspended_vendor_is_refused(live: Live) -> None:
    created = await live.api.vendor()
    from app.models import Vendor

    async with live.api.app.state.session_factory() as db:
        vendor = await db.get(Vendor, created.vendor.id)
        assert vendor is not None
        vendor.status = "suspended"
        await db.commit()
    assert "VENDOR_SUSPENDED" in await live.refused(created)


async def test_many_wrong_keys_get_the_ip_blocked(live: Live) -> None:
    created = await live.api.vendor()
    limit = live.api.settings.auth_fail_limit_per_minute
    for _ in range(limit + 1):
        await live.refused(created, apiKey="wab_live_nope_nope")
    assert "RATE_LIMITED" in await live.refused(created)  # even the right key, from this IP


async def test_a_second_device_cannot_take_an_active_line(live: Live) -> None:
    created = await live.api.vendor()
    first = await live.connect(created, device="device-0001")
    assert "SESSION_ALREADY_ACTIVE" in await live.refused(created, device="device-0002")
    assert first.client.connected  # the first one is untouched


async def test_same_device_reconnecting_replaces_its_own_ghost(live: Live) -> None:
    created = await live.api.vendor()
    old = await live.connect(created, device="device-0001")
    new = await live.connect(created, device="device-0001")
    await asyncio.wait_for(old.kicked.wait(), 5)
    assert new.client.connected
    redis = live.api.app.state.redis
    assert await presence.holder_sid(redis, created.vendor.id) is not None


async def test_disconnect_frees_the_line(live: Live) -> None:
    created = await live.api.vendor()
    plugin = await live.connect(created)
    await plugin.client.disconnect()
    redis = live.api.app.state.redis

    async def free() -> bool:
        return await presence.holder_sid(redis, created.vendor.id) is None

    await eventually(free)
    await live.connect(created, device="device-0002")  # another computer may take over now


async def test_job_travels_to_the_plugin_and_the_result_comes_back(live: Live) -> None:
    api = live.api
    created = await api.vendor(status_webhook_url="http://hook.test/s")
    plugin = await live.connect(created)
    assert (await plugin.heartbeat("connected"))["ok"] is True
    job_id = (await api.ob.post(created.secrets.live_key, message="Salam, OTP 1234")).json()[
        "jobId"
    ]
    await api.ob.dispatch(created.vendor)
    await plugin.wait_for_jobs(1)

    job = plugin.jobs[0]
    assert job["jobId"] == job_id and job["attempt"] == 1
    assert job["to"] == "923001234567" and job["message"] == "Salam, OTP 1234"
    assert await api.ob.status(job_id) == "dispatched"

    assert (await plugin.ack(job_id, 1))["ok"] is True
    assert await api.ob.status(job_id) == "sending"
    reply = await plugin.result(job_id, 1, "sent", waAck=1)
    assert reply == {"ok": True, "outcome": "applied"}
    assert await api.ob.status(job_id) == "sent"
    assert [r.event for r in await api.ob.deliveries(created.vendor.id)] == ["message.sent"]


async def test_jobs_wait_until_whatsapp_is_connected(live: Live) -> None:
    api = live.api
    created = await api.vendor()
    plugin = await live.connect(created)
    await plugin.heartbeat("logged_out")  # WhatsApp asks for a QR code
    await api.ob.post(created.secrets.live_key)
    await api.ob.dispatch(created.vendor)
    await asyncio.sleep(0.2)
    assert plugin.jobs == []

    api.ob.scheduler.calls.clear()
    await plugin.heartbeat("connected")  # back: the hub wakes dispatch by itself
    assert api.ob.scheduler.names() == ["dispatch"]
    await api.ob.drain()
    await plugin.wait_for_jobs(1)


async def test_connecting_wakes_dispatch(live: Live) -> None:
    api = live.api
    created = await api.vendor()
    api.ob.scheduler.calls.clear()
    await live.connect(created)
    assert api.ob.scheduler.names() == ["dispatch"]


async def test_a_vendors_jobs_never_reach_another_vendors_plugin(live: Live) -> None:
    api = live.api
    a, b = await api.vendor(), await api.vendor()
    plugin_a, plugin_b = await live.connect(a), await live.connect(b)
    await plugin_a.heartbeat()
    await plugin_b.heartbeat()
    await api.ob.post(a.secrets.live_key)
    await api.ob.dispatch(a.vendor)
    await plugin_a.wait_for_jobs(1)
    await asyncio.sleep(0.3)
    assert plugin_b.jobs == []


async def test_a_plugin_cannot_report_on_another_vendors_job(live: Live) -> None:
    api = live.api
    a, b = await api.vendor(), await api.vendor()
    plugin_a, plugin_b = await live.connect(a), await live.connect(b)
    await plugin_a.heartbeat()
    job_id = (await api.ob.post(a.secrets.live_key)).json()["jobId"]
    await api.ob.dispatch(a.vendor)
    await plugin_a.wait_for_jobs(1)
    assert (await plugin_b.ack(job_id, 1)) == {"ok": False, "error": "UNKNOWN_JOB"}
    assert (await plugin_b.result(job_id, 1, "sent")) == {"ok": False, "error": "UNKNOWN_JOB"}
    assert await api.ob.status(job_id) == "dispatched"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"jobId": "../etc/passwd", "attempt": 1, "status": "sent"},
        {"jobId": "job_0123456789abcdef", "attempt": -1, "status": "sent"},
        {"jobId": "job_0123456789abcdef", "attempt": 1, "status": "delivered"},
        {"jobId": "job_0123456789abcdef", "attempt": 1, "status": "sent", "waAck": 99},
    ],
)
async def test_malformed_results_are_refused_without_a_crash(
    live: Live, body: dict[str, Any]
) -> None:
    created = await live.api.vendor()
    plugin = await live.connect(created)
    reply = dict(await plugin.client.call("job_result", body, timeout=5))
    assert reply == {"ok": False, "error": "INVALID_PAYLOAD"}
    assert (await plugin.heartbeat())["ok"] is True  # the line is still fine


async def test_heartbeat_keeps_the_line_and_records_wa_state(live: Live) -> None:
    created = await live.api.vendor()
    plugin = await live.connect(created)
    await plugin.heartbeat("connected")
    redis = live.api.app.state.redis
    assert await presence.wa_state(redis, created.vendor.id) == "connected"
    await plugin.heartbeat("logged_out")
    assert await presence.wa_state(redis, created.vendor.id) == "logged_out"


async def test_heartbeat_after_the_line_was_lost_tells_the_plugin(live: Live) -> None:
    created = await live.api.vendor()
    plugin = await live.connect(created)
    await live.api.app.state.redis.delete(f"presence:vendor:{created.vendor.id}")
    reply = await plugin.heartbeat()
    assert reply == {"ok": False, "error": "SESSION_LOST"}
    await asyncio.wait_for(plugin.kicked.wait(), 5)


async def test_kick_vendor_drops_the_plugin(live: Live) -> None:
    created = await live.api.vendor()
    plugin = await live.connect(created)
    assert await live.api.app.state.hub.kick_vendor(created.vendor.id) is True
    await asyncio.wait_for(plugin.kicked.wait(), 5)
    assert await live.api.app.state.hub.kick_vendor(created.vendor.id) is False


async def test_nothing_secret_is_logged_by_the_hub(
    live: Live, capsys: pytest.CaptureFixture[str]
) -> None:
    created = await live.api.vendor()
    plugin = await live.connect(created)
    await plugin.heartbeat()
    job_id = (
        await live.api.ob.post(created.secrets.live_key, message="OTP 998877 secret text")
    ).json()["jobId"]
    await live.api.ob.dispatch(created.vendor)
    await plugin.wait_for_jobs(1)
    await plugin.ack(job_id, 1)
    await plugin.result(job_id, 1, "sent", waAck=1)
    logs = capsys.readouterr().out
    assert created.secrets.live_key not in logs
    assert "998877" not in logs and "secret text" not in logs
