"""Admin pages for the outbound side: devices, jobs (masked), daily cap, webhooks, and the
effect of suspending a vendor or revoking its key on a connected plugin."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.security import KeyKind
from app.models import AdminRole, ApiKey, Vendor
from app.models.outbound import Device, WebhookDelivery
from app.services import api_keys as key_service
from app.services import presence
from tests.integration.conftest import Api, MakeApi
from tests.integration.live_support import Live
from tests.integration.test_admin import audit_actions, logged_in, post

OTP_TEXT = "Aap ka OTP 482913 hai."
NUMBER = "923001234567"


async def add_device(api: Api, vendor: Vendor, device_id: str = "dev-0001") -> None:
    async with api.app.state.session_factory() as db:
        db.add(
            Device(
                vendor_id=vendor.id,
                device_id=device_id,
                wa_number=vendor.phone_e164,
                plugin_version="2.0.0",
            )
        )
        await db.commit()


# --- devices ---------------------------------------------------------------------------------


async def test_devices_show_online_state_and_whatsapp_state(make_api: MakeApi) -> None:
    api = await make_api()
    await logged_in(api)
    created = await api.vendor()
    await add_device(api, created.vendor)
    await add_device(api, created.vendor, "dev-other")
    await api.ob.online(created.vendor, wa_state="logged_out", device="dev-0001")

    page = await api.client.get(f"/admin/vendors/{created.vendor.id}/devices")
    assert page.status_code == 200
    assert 'http-equiv="refresh"' in page.text
    assert page.text.count("online</span>") >= 1
    assert "logged_out" in page.text
    assert page.text.count("offline</span>") == 1  # the second device does not hold the line


async def test_devices_empty_state(make_api: MakeApi) -> None:
    api = await make_api()
    await logged_in(api)
    created = await api.vendor()
    page = await api.client.get(f"/admin/vendors/{created.vendor.id}/devices")
    assert "No plugin has connected" in page.text


async def test_the_vendor_page_shows_devices_and_recent_messages(make_api: MakeApi) -> None:
    api = await make_api()
    await logged_in(api)
    created = await api.vendor()
    await add_device(api, created.vendor)
    page = await api.client.get(f"/admin/vendors/{created.vendor.id}")
    assert "Plugin (devices)" in page.text
    assert "Recent messages to customers" in page.text
    assert "Marketing daily cap" in page.text


# --- jobs, masked ----------------------------------------------------------------------------


async def test_jobs_page_masks_numbers_and_texts(make_api: MakeApi) -> None:
    api = await make_api()
    await logged_in(api)
    created = await api.vendor()
    key = created.secrets.live_key
    otp = (await api.ob.post(key, number=NUMBER, message=OTP_TEXT, type="otp", idem="a-1")).json()
    api.ob.clock.advance(1)
    plain = await api.ob.post(
        key, number=NUMBER, message="Aap ka order 998877 ship ho gaya", type="transactional"
    )

    for path in (f"/admin/vendors/{created.vendor.id}/jobs", f"/admin/vendors/{created.vendor.id}"):
        page = await api.client.get(path)
        assert page.status_code == 200
        assert otp["jobId"] in page.text
        assert plain.json()["jobId"] in page.text
        assert "OTP ****13" in page.text
        assert "transactional (32 characters)" in page.text
        for secret in ("482913", "998877", "ship ho gaya", NUMBER, "923001234567"):
            assert secret not in page.text, f"{secret!r} leaked on {path}"
        assert "+9230*****567" in page.text


async def test_jobs_can_be_filtered_by_status(make_api: MakeApi) -> None:
    api = await make_api()
    await logged_in(api)
    created = await api.vendor()
    job = (await api.ob.post(created.secrets.live_key, type="transactional")).json()
    queued = await api.client.get(
        f"/admin/vendors/{created.vendor.id}/jobs", params={"status": "queued"}
    )
    assert job["jobId"] in queued.text
    sent = await api.client.get(
        f"/admin/vendors/{created.vendor.id}/jobs", params={"status": "sent"}
    )
    assert job["jobId"] not in sent.text
    bad = await api.client.get(
        f"/admin/vendors/{created.vendor.id}/jobs", params={"status": "x'; drop"}
    )
    assert bad.status_code == 400


async def test_jobs_of_another_vendor_never_show(make_api: MakeApi) -> None:
    api = await make_api()
    await logged_in(api)
    first, second = await api.vendor(), await api.vendor()
    job = (await api.ob.post(first.secrets.live_key, type="transactional")).json()
    page = await api.client.get(f"/admin/vendors/{second.vendor.id}/jobs")
    assert job["jobId"] not in page.text


async def test_unknown_vendor_pages_are_404(make_api: MakeApi) -> None:
    api = await make_api()
    await logged_in(api)
    missing = "00000000-0000-0000-0000-000000000000"
    for page in ("devices", "jobs", "webhooks"):
        assert (await api.client.get(f"/admin/vendors/{missing}/{page}")).status_code == 404


# --- daily cap -------------------------------------------------------------------------------


async def test_cap_edit_is_saved_and_audited(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    created = await api.vendor()
    response = await post(
        api, f"/admin/vendors/{created.vendor.id}/cap", csrf, marketing_daily_cap="500"
    )
    assert response.status_code == 303
    async with api.app.state.session_factory() as db:
        vendor = await db.get(Vendor, created.vendor.id)
        assert vendor is not None
        assert vendor.marketing_daily_cap == 500
    assert "vendor.cap_change" in await audit_actions(api)


async def test_the_new_cap_is_used_by_the_api(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    created = await api.vendor(marketing_daily_cap=1)
    key = created.secrets.live_key
    assert (await api.ob.post(key, type="marketing", message="sale")).status_code == 202
    assert (await api.ob.post(key, type="marketing", message="sale 2")).status_code == 429
    await post(api, f"/admin/vendors/{created.vendor.id}/cap", csrf, marketing_daily_cap="2")
    assert (await api.ob.post(key, type="marketing", message="sale 3")).status_code == 202
    page = await api.client.get(f"/admin/vendors/{created.vendor.id}")
    assert "2 of 2" in page.text


async def test_cap_rejects_bad_values_and_support_cannot_change_it(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    created = await api.vendor()
    for bad in ("-1", "abc", "", "100001", "1.5"):
        response = await post(
            api, f"/admin/vendors/{created.vendor.id}/cap", csrf, marketing_daily_cap=bad
        )
        assert response.status_code == 400, bad
    assert "vendor.cap_change" not in await audit_actions(api)


async def test_support_cannot_change_the_cap(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api, AdminRole.SUPPORT)
    created = await api.vendor()
    denied = await post(
        api, f"/admin/vendors/{created.vendor.id}/cap", csrf, marketing_daily_cap="5"
    )
    assert denied.status_code == 403


# --- webhook deliveries ----------------------------------------------------------------------


async def add_delivery(api: Api, vendor: Vendor, status: str = "failed") -> int:
    async with api.app.state.session_factory() as db:
        row = WebhookDelivery(
            vendor_id=vendor.id,
            job_id="job_0123456789abcdef",
            event_id=f"evt_{abs(hash((vendor.id, status))) % 10**8:08d}",
            event="message.sent",
            payload={"eventId": "x"},
            status=status,
            attempts=6,
            last_status_code=500,
            last_error="HTTP_500",
            created_at=datetime.now(UTC),
        )
        db.add(row)
        await db.commit()
        return int(row.id)


async def test_webhook_page_lists_deliveries_and_replay_resets_one(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    created = await api.vendor()
    delivery_id = await add_delivery(api, created.vendor)

    page = await api.client.get(f"/admin/vendors/{created.vendor.id}/webhooks")
    assert "message.sent" in page.text
    assert "HTTP_500" in page.text
    assert "Replay" in page.text

    response = await post(
        api, f"/admin/vendors/{created.vendor.id}/webhooks/{delivery_id}/replay", csrf
    )
    assert response.status_code == 303
    async with api.app.state.session_factory() as db:
        row = await db.get(WebhookDelivery, delivery_id)
        assert row is not None
        assert (row.status, row.attempts, row.last_error) == ("pending", 0, None)
    assert api.ob.scheduler.count("deliver_webhook") == 1
    assert "webhook.replay" in await audit_actions(api)


async def test_replay_refuses_a_pending_one_and_another_vendors_delivery(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    first, second = await api.vendor(), await api.vendor()
    pending = await add_delivery(api, first.vendor, status="pending")
    assert (
        await post(api, f"/admin/vendors/{first.vendor.id}/webhooks/{pending}/replay", csrf)
    ).status_code == 409
    failed = await add_delivery(api, first.vendor, status="failed")
    assert (
        await post(api, f"/admin/vendors/{second.vendor.id}/webhooks/{failed}/replay", csrf)
    ).status_code == 409
    assert (
        await post(api, f"/admin/vendors/{first.vendor.id}/webhooks/999999/replay", csrf)
    ).status_code == 409


async def test_support_can_see_but_not_replay(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api, AdminRole.SUPPORT)
    created = await api.vendor()
    delivery_id = await add_delivery(api, created.vendor)
    page = await api.client.get(f"/admin/vendors/{created.vendor.id}/webhooks")
    assert page.status_code == 200
    assert "Replay" not in page.text
    assert (
        await post(api, f"/admin/vendors/{created.vendor.id}/webhooks/{delivery_id}/replay", csrf)
    ).status_code == 403


# --- suspending and revoking: queued messages are cancelled, the plugin is dropped -------------


async def test_suspend_cancels_queued_messages_and_drops_the_plugin(live: Live) -> None:
    api = live.api
    csrf = await logged_in(api)
    created = await api.vendor()
    plugin = await live.connect(created)
    await plugin.heartbeat("not_loaded")  # WhatsApp not ready: jobs stay queued
    key = created.secrets.live_key
    ids = [
        (await api.ob.post(key, type="transactional", message=f"m{i}")).json()["jobId"]
        for i in range(3)
    ]

    response = await post(
        api, f"/admin/vendors/{created.vendor.id}/status", csrf, action="suspend", confirm="on"
    )
    assert response.status_code == 303
    await asyncio.wait_for(plugin.kicked.wait(), 5)
    assert [await api.ob.status(job_id) for job_id in ids] == ["cancelled"] * 3
    row = await api.ob.row(ids[0])
    assert row.body is None  # the text is not kept for a cancelled message
    assert row.finished_at is not None
    assert await presence.holder_sid(api.app.state.redis, created.vendor.id) is None
    assert any("3 queued messages cancelled" in a for a in await audit_targets(api))
    status = await api.client.get(f"/v1/message-to-customer/{ids[0]}", headers={"X-API-Key": key})
    assert status.status_code == 403  # the suspended vendor's keys stop working


async def audit_targets(api: Api) -> list[str]:
    from app.models import AuditLog

    async with api.app.state.session_factory() as db:
        return [t or "" for t in await db.scalars(select(AuditLog.target))]


async def test_suspend_leaves_a_message_that_is_already_in_the_air(live: Live) -> None:
    api = live.api
    csrf = await logged_in(api)
    created = await api.vendor()
    plugin = await live.connect(created)
    await plugin.heartbeat("connected")
    key = created.secrets.live_key
    first = (await api.ob.post(key, type="transactional", message="in the air")).json()["jobId"]
    await api.ob.drain()  # the test plays the Celery worker
    await plugin.wait_for_jobs(1)
    second = (await api.ob.post(key, type="transactional", message="waiting")).json()["jobId"]

    await post(
        api, f"/admin/vendors/{created.vendor.id}/status", csrf, action="suspend", confirm="on"
    )
    assert await api.ob.status(first) in {
        "dispatched",
        "sending",
    }  # only the plugin can say what happened
    assert await api.ob.status(second) == "cancelled"


async def test_revoking_the_live_key_cancels_queued_messages_and_drops_the_plugin(
    live: Live,
) -> None:
    api = live.api
    csrf = await logged_in(api)
    created = await api.vendor()
    plugin = await live.connect(created)
    await plugin.heartbeat("offline")
    job_id = (await api.ob.post(created.secrets.live_key, type="transactional")).json()["jobId"]
    async with api.app.state.session_factory() as db:
        key_id = await db.scalar(
            select(ApiKey.id).where(ApiKey.vendor_id == created.vendor.id, ApiKey.kind == "live")
        )

    response = await post(api, f"/admin/vendors/{created.vendor.id}/keys/{key_id}/revoke", csrf)
    assert response.status_code == 303
    await asyncio.wait_for(plugin.kicked.wait(), 5)
    assert await api.ob.status(job_id) == "cancelled"
    refused = await live.refused(created)
    assert "INVALID_KEY" in refused  # the old key cannot come back


async def test_revoking_a_test_key_does_not_touch_the_plugin_or_the_queue(live: Live) -> None:
    api = live.api
    csrf = await logged_in(api)
    created = await api.vendor()
    plugin = await live.connect(created)
    await plugin.heartbeat("offline")
    job_id = (await api.ob.post(created.secrets.live_key, type="transactional")).json()["jobId"]
    async with api.app.state.session_factory() as db:
        key_id = await db.scalar(
            select(ApiKey.id).where(ApiKey.vendor_id == created.vendor.id, ApiKey.kind == "test")
        )
    await post(api, f"/admin/vendors/{created.vendor.id}/keys/{key_id}/revoke", csrf)
    await asyncio.sleep(0.3)
    assert not plugin.kicked.is_set()
    assert await api.ob.status(job_id) == "queued"


async def test_revoking_one_of_two_live_keys_keeps_the_queue(live: Live) -> None:
    api = live.api
    csrf = await logged_in(api)
    created = await api.vendor()
    async with api.app.state.session_factory() as db:
        await key_service.issue_key(db, created.vendor.id, KeyKind.LIVE)
        await db.commit()
        first_key = await db.scalar(
            select(ApiKey.id)
            .where(ApiKey.vendor_id == created.vendor.id, ApiKey.kind == "live")
            .order_by(ApiKey.created_at)
        )
    job_id = (await api.ob.post(created.secrets.live_key, type="transactional")).json()["jobId"]
    await post(api, f"/admin/vendors/{created.vendor.id}/keys/{first_key}/revoke", csrf)
    assert await api.ob.status(job_id) == "queued"


async def test_rotating_the_live_key_drops_the_plugin_but_keeps_the_queue(live: Live) -> None:
    api = live.api
    csrf = await logged_in(api)
    created = await api.vendor()
    plugin = await live.connect(created)
    await plugin.heartbeat("offline")
    job_id = (await api.ob.post(created.secrets.live_key, type="transactional")).json()["jobId"]
    response = await post(api, f"/admin/vendors/{created.vendor.id}/keys/rotate", csrf, kind="live")
    assert response.status_code == 200
    await asyncio.wait_for(plugin.kicked.wait(), 5)
    assert await api.ob.status(job_id) == "queued"  # the vendor will connect with the new key


async def test_the_audit_never_holds_message_text(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    created = await api.vendor()
    await api.ob.post(created.secrets.live_key, type="transactional", message="secret text 5566")
    await post(
        api, f"/admin/vendors/{created.vendor.id}/status", csrf, action="suspend", confirm="on"
    )
    joined = " ".join(await audit_targets(api))
    assert "secret text" not in joined
    assert "5566" not in joined
