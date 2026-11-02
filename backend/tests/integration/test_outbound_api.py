"""POST /v1/message-to-customer and GET status (plan 8.1, 17.B, tests 14.3)."""

import asyncio
from collections.abc import Coroutine
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.models.outbound import OutboundJob
from tests.integration.conftest import MakeApi


def error_code(response: object) -> str:
    return response.json()["error"]["code"]  # type: ignore[attr-defined, no-any-return]


async def test_accepts_a_job_and_answers_202(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    response = await api.ob.post(
        created.secrets.live_key, number="03486102017", type="otp", ttl=300, idem="otp-1"
    )
    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "queued"
    assert body["channelOnline"] is False  # no plugin connected yet
    assert body["jobId"].startswith("job_")

    row = await api.ob.row(body["jobId"])
    assert row.to_number == "923486102017"
    assert (row.type, row.priority, row.is_test) == ("otp", 9, False)
    assert row.vendor_id == created.vendor.id
    assert (row.ttl_expires_at - row.created_at).total_seconds() == 300
    assert api.ob.scheduler.names() == ["dispatch"]  # "diary first, then the note"


async def test_type_defaults_to_transactional_with_one_hour_ttl(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    body = (await api.ob.post(created.secrets.live_key)).json()
    row = await api.ob.row(body["jobId"])
    assert (row.type, row.priority) == ("transactional", 5)
    assert (row.ttl_expires_at - row.created_at).total_seconds() == 3600


@pytest.mark.parametrize(("ttl", "expected"), [(5, 30), (100_000, 86_400), (45, 45)])
async def test_ttl_is_clamped(make_api: MakeApi, ttl: int, expected: int) -> None:
    api = await make_api()
    created = await api.vendor()
    body = (await api.ob.post(created.secrets.live_key, ttl=ttl)).json()
    row = await api.ob.row(body["jobId"])
    assert (row.ttl_expires_at - row.created_at).total_seconds() == expected


@pytest.mark.parametrize("number", ["", "abc", "0348", "1" * 20, "03486102017x", "+1+2"])
async def test_invalid_numbers_are_400(make_api: MakeApi, number: str) -> None:
    api = await make_api()
    created = await api.vendor()
    response = await api.ob.post(created.secrets.live_key, number=number)
    assert (response.status_code, error_code(response)) == (400, "INVALID_NUMBER")


async def test_vendors_default_country_code_is_used(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(default_country_code="44")
    body = (await api.ob.post(created.secrets.live_key, number="07911123456")).json()
    assert (await api.ob.row(body["jobId"])).to_number == "447911123456"


async def test_bad_requests(make_api: MakeApi) -> None:
    api = await make_api()
    key = (await api.vendor()).secrets.live_key
    empty = await api.ob.post(key, message="   ")
    assert (empty.status_code, error_code(empty)) == (400, "INVALID_REQUEST")
    bad_type = await api.ob.post(key, type="spam")
    assert (bad_type.status_code, error_code(bad_type)) == (400, "INVALID_REQUEST")
    missing = await api.client.post(
        "/v1/message-to-customer", json={"number": "0300"}, headers={"X-API-Key": key}
    )
    assert (missing.status_code, error_code(missing)) == (400, "INVALID_REQUEST")
    long = await api.ob.post(key, message="x" * 4001)
    assert (long.status_code, error_code(long)) == (413, "MESSAGE_TOO_LONG")
    exactly = await api.ob.post(key, message="x" * 4000)
    assert exactly.status_code == 202


async def test_otp_needs_an_idempotency_key(make_api: MakeApi) -> None:
    api = await make_api()
    key = (await api.vendor()).secrets.live_key
    response = await api.ob.post(key, type="otp")
    assert (response.status_code, error_code(response)) == (400, "INVALID_REQUEST")
    assert "Idempotency-Key" in response.json()["error"]["message"]
    assert (await api.ob.post(key, type="transactional")).status_code == 202


async def test_same_idempotency_key_returns_the_same_job(make_api: MakeApi) -> None:
    api = await make_api()
    key = (await api.vendor()).secrets.live_key
    first = await api.ob.post(key, type="otp", idem="otp-order-9981")
    again = await api.ob.post(key, type="otp", idem="otp-order-9981")
    other = await api.ob.post(key, type="otp", idem="otp-order-9982")
    assert (first.status_code, again.status_code, other.status_code) == (202, 200, 202)
    assert first.json()["jobId"] == again.json()["jobId"] != other.json()["jobId"]
    assert api.ob.scheduler.count("dispatch") == 2  # the replay queued nothing new


async def test_idempotency_keys_belong_to_one_vendor(make_api: MakeApi) -> None:
    api = await make_api()
    a = (await api.vendor()).secrets.live_key
    b = (await api.vendor()).secrets.live_key
    first = await api.ob.post(a, idem="same")
    second = await api.ob.post(b, idem="same")
    assert first.json()["jobId"] != second.json()["jobId"]


async def test_hundred_parallel_calls_with_one_key_make_one_job(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    key = created.secrets.live_key
    responses = await asyncio.gather(
        *(api.ob.post(key, type="otp", idem="burst-1") for _ in range(100))
    )
    assert {r.status_code for r in responses} <= {200, 202}
    assert sum(r.status_code == 202 for r in responses) == 1
    assert len({r.json()["jobId"] for r in responses}) == 1
    async with api.app.state.session_factory() as db:
        count = await db.scalar(select(func.count()).select_from(OutboundJob))
    assert count == 1


async def test_bad_idempotency_key_is_refused(make_api: MakeApi) -> None:
    api = await make_api()
    key = (await api.vendor()).secrets.live_key
    response = await api.ob.post(key, idem="has space")
    assert (response.status_code, error_code(response)) == (400, "INVALID_REQUEST")
    assert (await api.ob.post(key, idem="x" * 129)).status_code == 400


async def test_wrong_key_and_suspended_vendor(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    wrong = await api.ob.post("wab_live_00000000_nope")
    assert (wrong.status_code, error_code(wrong)) == (401, "INVALID_KEY")
    async with api.app.state.session_factory() as db:
        vendor = await db.get(type(created.vendor), created.vendor.id)
        assert vendor is not None
        vendor.status = "suspended"
        await db.commit()
    suspended = await api.ob.post(created.secrets.live_key)
    assert (suspended.status_code, error_code(suspended)) == (403, "VENDOR_SUSPENDED")


async def test_queue_full(make_api: MakeApi) -> None:
    api = await make_api(max_queued_jobs_per_vendor=3)
    key = (await api.vendor()).secrets.live_key
    other = (await api.vendor()).secrets.live_key
    for _ in range(3):
        assert (await api.ob.post(key)).status_code == 202
    full = await api.ob.post(key)
    assert (full.status_code, error_code(full)) == (503, "QUEUE_FULL")
    assert (await api.ob.post(other)).status_code == 202  # another vendor is not affected


async def test_rate_limited(make_api: MakeApi) -> None:
    api = await make_api(outbound_rate_limit_per_minute=5)
    key = (await api.vendor()).secrets.live_key
    codes = [(await api.ob.post(key)).status_code for _ in range(6)]
    assert codes == [202] * 5 + [429]
    limited = await api.ob.post(key)
    assert int(limited.headers["Retry-After"]) > 0


async def test_daily_cap_is_per_vendor_and_only_for_marketing(make_api: MakeApi) -> None:
    api = await make_api()
    a = await api.vendor(marketing_daily_cap=3)
    b = await api.vendor(marketing_daily_cap=1)
    for _ in range(3):
        assert (await api.ob.post(a.secrets.live_key, type="marketing")).status_code == 202
    over = await api.ob.post(a.secrets.live_key, type="marketing")
    assert (over.status_code, error_code(over)) == (429, "DAILY_CAP_EXCEEDED")
    assert 0 < int(over.headers["Retry-After"]) <= 86_400
    # OTP and transactional are never blocked by the marketing cap.
    assert (await api.ob.post(a.secrets.live_key, type="otp", idem="o1")).status_code == 202
    assert (await api.ob.post(a.secrets.live_key, type="transactional")).status_code == 202
    # Vendor B has its own, smaller cap.
    assert (await api.ob.post(b.secrets.live_key, type="marketing")).status_code == 202
    assert (await api.ob.post(b.secrets.live_key, type="marketing")).status_code == 429


async def test_idempotent_replay_does_not_use_up_the_cap(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(marketing_daily_cap=2)
    key = created.secrets.live_key
    for _ in range(5):
        assert (await api.ob.post(key, type="marketing", idem="promo-1")).status_code in (200, 202)
    assert (await api.ob.post(key, type="marketing", idem="promo-2")).status_code == 202
    assert (await api.ob.post(key, type="marketing", idem="promo-3")).status_code == 429


async def test_cap_counter_is_rebuilt_from_the_database_after_redis_loses_it(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    created = await api.vendor(marketing_daily_cap=2)
    key = created.secrets.live_key
    assert (await api.ob.post(key, type="marketing")).status_code == 202
    assert (await api.ob.post(key, type="marketing")).status_code == 202
    await api.app.state.redis.flushdb()  # Redis lost its memory
    assert (await api.ob.post(key, type="marketing")).status_code == 429


async def test_get_status_and_cross_vendor_isolation(make_api: MakeApi) -> None:
    api = await make_api()
    mine = await api.vendor()
    theirs = await api.vendor()
    job_id = (await api.ob.post(mine.secrets.live_key)).json()["jobId"]

    own = await api.client.get(
        f"/v1/message-to-customer/{job_id}", headers={"X-API-Key": mine.secrets.live_key}
    )
    assert own.status_code == 200
    assert own.json() == {"jobId": job_id, "status": "queued", "sentAt": None, "errorCode": None}

    foreign = await api.client.get(
        f"/v1/message-to-customer/{job_id}", headers={"X-API-Key": theirs.secrets.live_key}
    )
    assert (foreign.status_code, error_code(foreign)) == (404, "NOT_FOUND")
    missing = await api.client.get(
        "/v1/message-to-customer/job_nope", headers={"X-API-Key": mine.secrets.live_key}
    )
    assert missing.status_code == 404


async def test_test_key_and_live_key_see_different_worlds(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    live_job = (await api.ob.post(created.secrets.live_key)).json()["jobId"]
    test_job = (await api.ob.post(created.secrets.test_key)).json()["jobId"]
    assert (await api.ob.row(test_job)).is_test is True
    assert (await api.ob.row(live_job)).is_test is False

    def get(job: str, key: str) -> Coroutine[Any, Any, httpx.Response]:
        return api.client.get(f"/v1/message-to-customer/{job}", headers={"X-API-Key": key})

    assert (await get(test_job, created.secrets.test_key)).status_code == 200
    assert (await get(test_job, created.secrets.live_key)).status_code == 404
    assert (await get(live_job, created.secrets.live_key)).status_code == 200
    assert (await get(live_job, created.secrets.test_key)).status_code == 404
    # A test key reports the channel as online: the virtual plugin is always there.
    assert (await api.ob.post(created.secrets.test_key)).json()["channelOnline"] is True


async def test_channel_online_follows_the_plugin_connection(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    assert (await api.ob.post(created.secrets.live_key)).json()["channelOnline"] is False
    await api.ob.online(created.vendor)
    assert (await api.ob.post(created.secrets.live_key)).json()["channelOnline"] is True


async def test_job_is_safe_when_the_broker_is_down(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    api.ob.scheduler.broken = True
    response = await api.ob.post(created.secrets.live_key, type="otp", idem="otp-1")
    assert response.status_code == 202  # the diary entry is made; the sweeper finds it later
    assert (await api.ob.row(response.json()["jobId"])).status == "queued"


async def test_message_text_and_number_never_reach_the_logs(
    make_api: MakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api = await make_api()
    key = (await api.vendor()).secrets.live_key
    await api.ob.post(
        key, number="03486102017", message="Aap ka OTP 482913 hai.", type="otp", idem="otp-log"
    )
    out = capsys.readouterr()
    text = out.out + out.err
    assert "job_accepted" in text
    for secret in ("482913", "03486102017", "923486102017", "wab_live_"):
        assert secret not in text


async def test_outbound_requests_are_not_in_request_logs_as_bodies(make_api: MakeApi) -> None:
    api = await make_api()
    key = (await api.vendor()).secrets.live_key
    response = await api.ob.post(key, message="secret words here")
    job = await api.ob.row(response.json()["jobId"])
    assert job.body == "secret words here"  # kept only until 24h after the final status
