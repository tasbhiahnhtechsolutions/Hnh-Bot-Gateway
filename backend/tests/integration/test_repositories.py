import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import KeyKind, SecretBox, generate_api_key, generate_signing_secret
from app.models import ApiKey, ApiKeyStatus, InboundMode, OutboundMode, Vendor, VendorStatus
from app.repositories import api_keys, vendors

BOX = SecretBox(Fernet.generate_key())


def new_vendor(**overrides: object) -> Vendor:
    values: dict[str, object] = {
        "business_name": "Ali Boutique",
        "phone_e164": "923001234567",
        "email": "ali@example.com",
        "bot_base_url": "https://bot.example.com",
        "bot_secret_enc": BOX.encrypt(generate_signing_secret()),
        "webhook_secret_enc": BOX.encrypt(generate_signing_secret()),
    }
    values.update(overrides)
    return Vendor(**values)


def new_key(vendor_id: uuid.UUID, kind: KeyKind = KeyKind.LIVE) -> tuple[ApiKey, str]:
    generated = generate_api_key(kind)
    row = ApiKey(
        vendor_id=vendor_id,
        kind=kind.value,
        key_prefix=generated.prefix,
        key_hash=generated.key_hash,
    )
    return row, generated.full_key


async def test_vendor_defaults_match_the_plan(db: AsyncSession) -> None:
    vendor = await vendors.add(db, new_vendor())
    await db.refresh(vendor)
    assert vendor.status == VendorStatus.ACTIVE
    assert vendor.inbound_mode == InboundMode.FORWARD
    assert vendor.outbound_mode == OutboundMode.REAL_PLUGIN
    assert vendor.default_country_code == "92"
    assert vendor.marketing_daily_cap == 200
    assert vendor.created_at is not None


async def test_secrets_are_stored_encrypted(db: AsyncSession) -> None:
    secret = generate_signing_secret()
    vendor = await vendors.add(db, new_vendor(bot_secret_enc=BOX.encrypt(secret)))
    assert secret not in vendor.bot_secret_enc
    assert BOX.decrypt(vendor.bot_secret_enc) == secret


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "banned"},
        {"inbound_mode": "weird"},
        {"outbound_mode": "x"},
        {"marketing_daily_cap": -1},
    ],
)
async def test_vendor_check_constraints(db: AsyncSession, bad: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        await vendors.add(db, new_vendor(**bad))


async def test_get_and_list(db: AsyncSession) -> None:
    vendor = await vendors.add(db, new_vendor())
    assert (await vendors.get(db, vendor.id)) is vendor
    assert await vendors.get(db, uuid.uuid4()) is None
    assert vendor in await vendors.list_all(db)


async def test_find_active_key_by_prefix_and_verify(db: AsyncSession) -> None:
    from app.core.security import verify_api_key

    vendor = await vendors.add(db, new_vendor())
    row, full_key = new_key(vendor.id)
    await api_keys.add(db, row)

    found = await api_keys.find_active_by_prefix(db, row.key_prefix)
    assert found is not None
    assert found.vendor_id == vendor.id
    assert verify_api_key(full_key, found.key_hash)
    assert await api_keys.find_active_by_prefix(db, "00000000") is None


async def test_live_and_test_keys_for_one_vendor(db: AsyncSession) -> None:
    vendor = await vendors.add(db, new_vendor())
    for kind in (KeyKind.LIVE, KeyKind.TEST):
        await api_keys.add(db, new_key(vendor.id, kind)[0])
    kinds = {k.kind for k in await api_keys.list_for_vendor(db, vendor.id)}
    assert kinds == {"live", "test"}


async def test_revoked_key_is_not_found_and_revoke_is_not_repeatable(db: AsyncSession) -> None:
    vendor = await vendors.add(db, new_vendor())
    row, _ = new_key(vendor.id)
    await api_keys.add(db, row)

    assert await api_keys.revoke(db, row.id) is True
    assert await api_keys.find_active_by_prefix(db, row.key_prefix) is None
    assert await api_keys.revoke(db, row.id) is False
    await db.refresh(row)
    assert row.status == ApiKeyStatus.REVOKED
    assert row.revoked_at is not None


async def test_prefix_collision_is_rejected_by_the_database(db: AsyncSession) -> None:
    vendor = await vendors.add(db, new_vendor())
    first, _ = new_key(vendor.id)
    await api_keys.add(db, first)
    clash = ApiKey(vendor_id=vendor.id, kind="live", key_prefix=first.key_prefix, key_hash="0" * 64)
    with pytest.raises(IntegrityError):
        await api_keys.add(db, clash)


async def test_prefix_exists(db: AsyncSession) -> None:
    vendor = await vendors.add(db, new_vendor())
    row, _ = new_key(vendor.id)
    await api_keys.add(db, row)
    assert await api_keys.prefix_exists(db, row.key_prefix)
    assert not await api_keys.prefix_exists(db, "ffffffff")


async def test_touch_last_used(db: AsyncSession) -> None:
    vendor = await vendors.add(db, new_vendor())
    row, _ = new_key(vendor.id)
    await api_keys.add(db, row)
    assert row.last_used_at is None
    await api_keys.touch_last_used(db, row.id)
    await db.refresh(row)
    assert row.last_used_at is not None


async def test_key_must_belong_to_an_existing_vendor(db: AsyncSession) -> None:
    row, _ = new_key(uuid.uuid4())
    with pytest.raises(IntegrityError):
        await api_keys.add(db, row)


async def test_invalid_kind_rejected(db: AsyncSession) -> None:
    vendor = await vendors.add(db, new_vendor())
    with pytest.raises(IntegrityError):
        await api_keys.add(
            db, ApiKey(vendor_id=vendor.id, kind="prod", key_prefix="abcd1234", key_hash="0" * 64)
        )
