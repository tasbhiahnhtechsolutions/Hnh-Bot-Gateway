from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.core.security import KeyKind
from app.models import ApiKey, VendorStatus
from app.repositories import vendors
from app.services.api_keys import rotate_key
from tests.integration.conftest import MakeApi


async def whoami(client: httpx.AsyncClient, key: str | None) -> httpx.Response:
    headers = {} if key is None else {"X-API-Key": key}
    return await client.get("/v1/plugin/whoami", headers=headers)


async def test_whoami_returns_the_vendor_for_a_live_and_a_test_key(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(business_name="Ali Boutique", whatsapp_number="923001234567")

    live = await whoami(api.client, created.secrets.live_key)
    assert live.status_code == 200
    assert live.json() == {
        "vendorId": str(created.vendor.id),
        "businessName": "Ali Boutique",
        "whatsappNumber": "923001234567",
        "keyKind": "live",
        "inboundMode": "forward",
        "contractVersion": 1,
    }
    test = await whoami(api.client, created.secrets.test_key)
    assert test.status_code == 200
    assert test.json()["keyKind"] == "test"


async def test_wrong_keys_all_get_the_same_401(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    real = created.secrets.live_key
    tampered = real[:-3] + ("abc" if not real.endswith("abc") else "xyz")
    swapped_kind = real.replace("wab_live_", "wab_test_", 1)
    for key in [None, "", "garbage", "wab_live_", "wab_live_00000000_nope", tampered, swapped_kind]:
        response = await whoami(api.client, key)
        assert response.status_code == 401, key
        assert response.json() == {
            "error": {"code": "INVALID_KEY", "message": "API key is missing or not valid"}
        }


async def test_key_in_the_url_is_not_accepted(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    response = await api.client.get(f"/v1/plugin/whoami?api_key={created.secrets.live_key}")
    assert response.status_code == 401


async def test_revoked_key_stops_working_and_new_key_works(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    async with api.app.state.session_factory() as db:
        new_key = await rotate_key(db, created.vendor.id, KeyKind.LIVE)
        await db.commit()
    assert (await whoami(api.client, created.secrets.live_key)).status_code == 401
    assert (await whoami(api.client, new_key)).status_code == 200


async def test_suspended_vendor_gets_403(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    async with api.app.state.session_factory() as db:
        vendor = await vendors.get(db, created.vendor.id)
        assert vendor is not None
        vendor.status = VendorStatus.SUSPENDED.value
        await db.commit()
    response = await whoami(api.client, created.secrets.live_key)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "VENDOR_SUSPENDED"


async def test_last_used_is_recorded(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    before = datetime.now(UTC)
    await whoami(api.client, created.secrets.live_key)
    async with api.app.state.session_factory() as db:
        rows = (await db.scalars(select(ApiKey).where(ApiKey.kind == "live"))).all()
    assert rows[0].last_used_at is not None
    assert rows[0].last_used_at >= before


async def test_wrong_keys_are_rate_limited_per_ip_and_good_keys_are_blocked_too(
    make_api: MakeApi,
) -> None:
    api = await make_api(auth_fail_limit_per_minute=3)
    created = await api.vendor()
    for _ in range(3):
        assert (await whoami(api.client, "wab_live_00000000_wrong")).status_code == 401

    blocked = await whoami(api.client, "wab_live_00000000_wrong")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(blocked.headers["retry-after"]) <= 60
    # The IP is blocked as a whole, even for the right key, until the minute is over.
    assert (await whoami(api.client, created.secrets.live_key)).status_code == 429


async def test_successful_requests_do_not_count_towards_the_limit(make_api: MakeApi) -> None:
    api = await make_api(auth_fail_limit_per_minute=3)
    created = await api.vendor()
    for _ in range(10):
        assert (await whoami(api.client, created.secrets.live_key)).status_code == 200


async def test_unknown_route_uses_the_standard_error_format(make_api: MakeApi) -> None:
    api = await make_api()
    response = await api.client.get("/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
