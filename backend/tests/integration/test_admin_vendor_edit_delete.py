"""Admin: edit a vendor's details and delete a vendor."""

import uuid

from sqlalchemy import func, select

from app.models import AdminRole, ApiKey, Vendor
from tests.integration.conftest import Api, MakeApi
from tests.integration.test_admin import audit_actions, logged_in, post
from tests.integration.test_admin_outbound import audit_targets

EDIT = {
    "business_name": "Renamed Shop",
    "whatsapp_number": "923001234567",
    "email": "new@example.com",
    "bot_base_url": "https://bot.example.com/",
    "status_webhook_url": "",
    "default_country_code": "92",
    "internal_note": "changed",
}


async def reload(api: Api, vendor_id: uuid.UUID) -> Vendor | None:
    async with api.app.state.session_factory() as db:
        row: Vendor | None = await db.get(Vendor, vendor_id)
        return row


async def loaded(api: Api, vendor_id: uuid.UUID) -> Vendor:
    vendor = await reload(api, vendor_id)
    assert vendor is not None
    return vendor


async def test_edit_changes_the_fields_and_audits_only_their_names(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    created = await api.vendor()
    number = created.vendor.phone_e164

    page = await api.client.get(f"/admin/vendors/{created.vendor.id}/edit")
    assert page.status_code == 200
    assert created.vendor.business_name in page.text

    edit = {**EDIT, "whatsapp_number": number}
    response = await post(api, f"/admin/vendors/{created.vendor.id}/edit", csrf, **edit)
    assert response.status_code == 303
    vendor = await loaded(api, created.vendor.id)
    assert vendor.business_name == "Renamed Shop"
    assert vendor.email == "new@example.com"
    assert vendor.bot_base_url == "https://bot.example.com"
    assert vendor.internal_note == "changed"
    assert "vendor.edit" in await audit_actions(api)
    targets = " ".join(await audit_targets(api))
    assert "business_name" in targets and "email" in targets
    assert "Renamed Shop" not in targets and "new@example.com" not in targets


async def test_edit_with_bad_input_changes_nothing(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    created = await api.vendor()
    bad = {**EDIT, "whatsapp_number": created.vendor.phone_e164, "email": "not-an-email"}
    response = await post(api, f"/admin/vendors/{created.vendor.id}/edit", csrf, **bad)
    assert response.status_code == 400
    assert "email" in response.text
    assert (await loaded(api, created.vendor.id)).business_name == created.vendor.business_name


async def test_edit_refuses_a_number_another_active_vendor_has(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    first = await api.vendor()
    second = await api.vendor(whatsapp_number="923009998877")
    edit = {**EDIT, "whatsapp_number": first.vendor.phone_e164}
    response = await post(api, f"/admin/vendors/{second.vendor.id}/edit", csrf, **edit)
    assert response.status_code == 409
    assert (await loaded(api, second.vendor.id)).phone_e164 == "923009998877"


async def test_support_admin_cannot_edit_or_delete(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api, AdminRole.SUPPORT)
    created = await api.vendor()
    assert (await api.client.get(f"/admin/vendors/{created.vendor.id}/edit")).status_code == 403
    edit = {**EDIT, "whatsapp_number": created.vendor.phone_e164}
    assert (
        await post(api, f"/admin/vendors/{created.vendor.id}/edit", csrf, **edit)
    ).status_code == 403
    deleted = await post(
        api,
        f"/admin/vendors/{created.vendor.id}/delete",
        csrf,
        confirm_name=created.vendor.business_name,
    )
    assert deleted.status_code == 403
    assert await reload(api, created.vendor.id) is not None


async def test_delete_needs_the_exact_name(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    created = await api.vendor()
    response = await post(
        api, f"/admin/vendors/{created.vendor.id}/delete", csrf, confirm_name="wrong"
    )
    assert response.status_code == 400
    assert await reload(api, created.vendor.id) is not None


async def test_delete_removes_the_vendor_and_its_keys(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    created = await api.vendor()
    key = created.secrets.live_key
    response = await post(
        api,
        f"/admin/vendors/{created.vendor.id}/delete",
        csrf,
        confirm_name=created.vendor.business_name,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/vendors?notice=vendor_deleted"
    assert await reload(api, created.vendor.id) is None
    async with api.app.state.session_factory() as db:
        keys = await db.scalar(
            select(func.count()).select_from(ApiKey).where(ApiKey.vendor_id == created.vendor.id)
        )
    assert keys == 0
    whoami = await api.client.get("/v1/plugin/whoami", headers={"X-API-Key": key})
    assert whoami.status_code == 401
    assert "vendor.delete" in await audit_actions(api)
    assert (await api.client.get(f"/admin/vendors/{created.vendor.id}")).status_code == 404
