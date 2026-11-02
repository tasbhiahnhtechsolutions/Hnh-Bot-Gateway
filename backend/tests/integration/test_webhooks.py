"""Status webhooks: signed, retried, never into a forbidden address (plan 17.B.8, 14.4)."""

import json
from datetime import timedelta

import httpx

from app.core.signing import verify_signature
from app.services import webhooks
from app.services.job_rules import WEBHOOK_MAX_ATTEMPTS, WEBHOOK_RETRY_DELAYS
from app.services.vendors import CreatedVendor
from tests.integration.conftest import Api, MakeApi

HOOK = "http://hook.test/status"


class Hook:
    def __init__(self, statuses: list[int] | None = None, error: Exception | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.statuses = statuses or [200]
        self.error = error
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        index = min(len(self.requests) - 1, len(self.statuses) - 1)
        return httpx.Response(self.statuses[index])


async def sent_job(api: Api, hook: Hook, **vendor: object) -> tuple[CreatedVendor, str]:
    created = await api.vendor(status_webhook_url=HOOK, **vendor)
    api.ob.hook_transport = hook.transport
    await api.ob.online(created.vendor)
    job_id = (await api.ob.post(created.secrets.live_key, number="0300 123 4567")).json()["jobId"]
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    return created, job_id


async def test_delivery_is_signed_with_the_vendors_secret(make_api: MakeApi) -> None:
    api = await make_api()
    hook = Hook()
    created, job_id = await sent_job(api, hook)
    await api.ob.drain()
    assert len(hook.requests) == 1
    request = hook.requests[0]
    verify_signature(created.secrets.webhook_secret, request.headers, request.content)
    body = json.loads(request.content)
    assert body["event"] == "message.sent"
    assert body["jobId"] == job_id
    assert body["number"] == "923001234567"
    assert body["status"] == "sent" and body["errorCode"] is None
    assert body["eventId"] == request.headers["X-Event-Id"]
    rows = await api.ob.deliveries(created.vendor.id)
    assert [(r.status, r.attempts) for r in rows] == [("delivered", 1)]


async def test_a_wrong_secret_does_not_verify(make_api: MakeApi) -> None:
    import pytest

    from app.core.signing import SignatureError

    api = await make_api()
    hook = Hook()
    await sent_job(api, hook)
    await api.ob.drain()
    request = hook.requests[0]
    with pytest.raises(SignatureError):
        verify_signature("whsec_not_the_secret", request.headers, request.content)


async def test_failed_delivery_is_retried_on_the_documented_schedule(make_api: MakeApi) -> None:
    api = await make_api()
    hook = Hook(statuses=[500])
    created, _ = await sent_job(api, hook)
    delays: list[float] = []
    for _ in range(WEBHOOK_MAX_ATTEMPTS):
        call = next(c for c in api.ob.scheduler.calls if c.name == "deliver_webhook")
        api.ob.scheduler.calls.remove(call)
        async with api.app.state.session_factory() as db:
            result = await webhooks.attempt_delivery(
                db,
                call.args[0],
                settings=api.settings,
                box=api.box,
                transport=hook.transport,
                now=api.ob.clock.dt,
            )
        if result is None:
            break
        delays.append(result)
        api.ob.clock.advance(result)
        await api.ob.scheduler.deliver_webhook(call.args[0], result)
    assert delays == [float(d) for d in WEBHOOK_RETRY_DELAYS]
    assert len(hook.requests) == WEBHOOK_MAX_ATTEMPTS
    row = (await api.ob.deliveries(created.vendor.id))[0]
    assert (row.status, row.attempts, row.last_error) == (
        "failed",
        WEBHOOK_MAX_ATTEMPTS,
        "HTTP_500",
    )


async def test_retry_succeeds_after_a_hiccup_and_keeps_the_same_event_id(make_api: MakeApi) -> None:
    api = await make_api()
    hook = Hook(statuses=[503, 200])
    created, _ = await sent_job(api, hook)
    await api.ob.drain(delayed=True)
    assert len(hook.requests) == 2
    assert hook.requests[0].headers["X-Event-Id"] == hook.requests[1].headers["X-Event-Id"]
    assert (await api.ob.deliveries(created.vendor.id))[0].status == "delivered"


async def test_network_errors_and_timeouts_are_retried(make_api: MakeApi) -> None:
    api = await make_api()
    hook = Hook(error=httpx.ConnectTimeout("slow"))
    created, _ = await sent_job(api, hook)
    async with api.app.state.session_factory() as db:
        delay = await webhooks.attempt_delivery(
            db,
            (await api.ob.deliveries(created.vendor.id))[0].id,
            settings=api.settings,
            box=api.box,
            transport=hook.transport,
            now=api.ob.clock.dt,
        )
    assert delay == float(WEBHOOK_RETRY_DELAYS[0])
    row = (await api.ob.deliveries(created.vendor.id))[0]
    assert (row.status, row.last_error) == ("pending", "TIMEOUT")


async def test_a_delivery_is_not_sent_twice_when_run_twice(make_api: MakeApi) -> None:
    api = await make_api()
    hook = Hook()
    await sent_job(api, hook)
    call = next(c for c in api.ob.scheduler.calls if c.name == "deliver_webhook")
    for _ in range(3):
        async with api.app.state.session_factory() as db:
            await webhooks.attempt_delivery(
                db,
                call.args[0],
                settings=api.settings,
                box=api.box,
                transport=hook.transport,
                now=api.ob.clock.dt,
            )
    assert len(hook.requests) == 1


async def test_no_webhook_url_means_no_delivery_row(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    await api.ob.online(created.vendor)
    job_id = (await api.ob.post(created.secrets.live_key)).json()["jobId"]
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    assert await api.ob.deliveries(created.vendor.id) == []


async def test_failure_webhook_carries_the_public_error_code(make_api: MakeApi) -> None:
    api = await make_api()
    hook = Hook()
    created = await api.vendor(status_webhook_url=HOOK)
    api.ob.hook_transport = hook.transport
    await api.ob.online(created.vendor)
    job_id = (await api.ob.post(created.secrets.live_key)).json()["jobId"]
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "failed", error_code="NOT_ON_WHATSAPP")
    await api.ob.drain()
    body = json.loads(hook.requests[0].content)
    assert (body["event"], body["status"], body["errorCode"]) == (
        "message.failed",
        "failed",
        "NOT_ON_WHATSAPP",
    )
    assert "message" not in body and "body" not in body  # the text never leaves in a webhook


async def test_retried_job_does_not_tell_the_vendor_about_every_attempt(make_api: MakeApi) -> None:
    api = await make_api()
    hook = Hook()
    created = await api.vendor(status_webhook_url=HOOK)
    api.ob.hook_transport = hook.transport
    await api.ob.online(created.vendor)
    job_id = (await api.ob.post(created.secrets.live_key)).json()["jobId"]
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "failed", error_code="SEND_FAILED")
    assert await api.ob.deliveries(created.vendor.id) == []  # it is only queued again


async def test_replay_gives_a_finished_delivery_a_fresh_start(make_api: MakeApi) -> None:
    api = await make_api()
    hook = Hook(statuses=[500, 500, 500, 500, 500, 500, 200])
    created, _ = await sent_job(api, hook)
    await api.ob.drain(delayed=True)
    row = (await api.ob.deliveries(created.vendor.id))[0]
    assert row.status == "failed"
    async with api.app.state.session_factory() as db:
        assert await webhooks.replay(db, row.id, created.vendor.id)
        assert not await webhooks.replay(db, row.id, created.vendor.id)  # now pending again
    await api.ob.scheduler.deliver_webhook(row.id)
    await api.ob.drain()
    assert (await api.ob.deliveries(created.vendor.id))[0].status == "delivered"


async def test_replay_of_another_vendors_delivery_is_refused(make_api: MakeApi) -> None:
    import uuid

    api = await make_api()
    created, _ = await sent_job(api, Hook(statuses=[200]))
    await api.ob.drain()
    row = (await api.ob.deliveries(created.vendor.id))[0]
    async with api.app.state.session_factory() as db:
        assert not await webhooks.replay(db, row.id, uuid.uuid4())


async def test_webhook_url_is_checked_again_at_send_time(make_api: MakeApi) -> None:
    """DNS can change after the vendor was saved: a URL that now points inside is refused
    and not retried."""
    api = await make_api(allow_private_bot_urls=False)
    created = await api.vendor(status_webhook_url="https://hook.example.com/status")
    hook = Hook()
    api.ob.hook_transport = hook.transport
    await api.ob.online(created.vendor)
    job_id = (await api.ob.post(created.secrets.live_key)).json()["jobId"]
    await api.ob.dispatch(created.vendor)
    await api.ob.started(created.vendor, job_id, 1)
    await api.ob.result(created.vendor, job_id, 1, "sent", wa_ack=1)
    row = (await api.ob.deliveries(created.vendor.id))[0]

    async def inside(_host: str) -> list[str]:
        return ["10.0.0.5"]

    async with api.app.state.session_factory() as db:
        assert (
            await webhooks.attempt_delivery(
                db,
                row.id,
                settings=api.settings,
                box=api.box,
                transport=hook.transport,
                resolver=inside,
                now=api.ob.clock.dt + timedelta(seconds=1),
            )
            is None
        )
    assert hook.requests == []
    row = (await api.ob.deliveries(created.vendor.id))[0]
    assert (row.status, row.last_error) == ("failed", "URL_NOT_ALLOWED")
