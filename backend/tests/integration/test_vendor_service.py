import asyncio
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import ApiError
from app.core.security import KeyKind, SecretBox, parse_api_key, verify_api_key
from app.models import ApiKey, Vendor, VendorStatus
from app.repositories import api_keys
from app.schemas.vendor import VendorCreate
from app.services.api_keys import rotate_key
from app.services.bot_url import Resolver, resolve_host
from app.services.vendors import CreatedVendor, create_vendor

BOX = SecretBox(Fernet.generate_key())


def data(**overrides: object) -> VendorCreate:
    values: dict[str, object] = {
        "business_name": "Ali Boutique",
        "whatsapp_number": "923001234567",
        "email": "ali@example.com",
        "bot_base_url": "http://localhost:9000/",
    }
    values.update(overrides)
    return VendorCreate(**values)


async def create(
    db: AsyncSession,
    spec: VendorCreate | None = None,
    *,
    allow: bool = True,
    resolver: Resolver = resolve_host,
) -> CreatedVendor:
    return await create_vendor(
        db, spec or data(), box=BOX, allow_private_bot_urls=allow, resolver=resolver
    )


async def test_create_vendor_issues_working_keys_and_encrypted_secrets(db: AsyncSession) -> None:
    created = await create(db)
    vendor, secrets = created.vendor, created.secrets

    live = parse_api_key(secrets.live_key)
    test = parse_api_key(secrets.test_key)
    assert live is not None
    assert live.kind is KeyKind.LIVE
    assert test is not None
    assert test.kind is KeyKind.TEST

    row = await api_keys.find_active_by_prefix(db, live.prefix)
    assert row is not None
    assert row.vendor_id == vendor.id
    assert verify_api_key(secrets.live_key, row.key_hash)

    assert secrets.bot_secret not in vendor.bot_secret_enc
    assert BOX.decrypt(vendor.bot_secret_enc) == secrets.bot_secret
    assert BOX.decrypt(vendor.webhook_secret_enc) == secrets.webhook_secret
    assert secrets.bot_secret != secrets.webhook_secret
    assert vendor.bot_base_url == "http://localhost:9000"  # trailing slash trimmed
    assert vendor.vendor_context == {}


async def test_no_plain_secret_is_stored_anywhere(db: AsyncSession) -> None:
    created = await create(db)
    stored = {
        created.vendor.bot_secret_enc,
        created.vendor.webhook_secret_enc,
        *[k.key_hash for k in await api_keys.list_for_vendor(db, created.vendor.id)],
    }
    for secret in (
        created.secrets.live_key,
        created.secrets.test_key,
        created.secrets.bot_secret,
        created.secrets.webhook_secret,
    ):
        assert all(secret not in value for value in stored)


async def test_duplicate_active_number_rejected(db: AsyncSession) -> None:
    await create(db)
    with pytest.raises(ApiError) as info:
        await create(db, data(business_name="Other"))
    assert info.value.status_code == 409
    assert info.value.code == "VENDOR_NUMBER_IN_USE"


async def test_number_of_a_suspended_vendor_can_be_reused(db: AsyncSession) -> None:
    first = await create(db)
    first.vendor.status = VendorStatus.SUSPENDED.value
    await db.flush()
    second = await create(db, data(business_name="New owner"))
    assert second.vendor.id != first.vendor.id


async def test_prod_style_rules_apply_to_bot_url(db: AsyncSession) -> None:
    async def public(_host: str) -> list[str]:
        return ["93.184.216.34"]

    with pytest.raises(ApiError) as info:
        await create(db, allow=False)  # localhost bot URL, private not allowed
    assert info.value.code == "BOT_URL_MUST_BE_HTTPS"

    ok = await create(
        db, data(bot_base_url="https://bot.example.com"), allow=False, resolver=public
    )
    assert ok.vendor.bot_base_url == "https://bot.example.com"


async def test_vendor_context_is_stored(db: AsyncSession) -> None:
    created = await create(db, data(vendor_context={"clinicId": "gulberg"}))
    assert created.vendor.vendor_context == {"clinicId": "gulberg"}


async def test_webhook_url_is_validated_too(db: AsyncSession) -> None:
    with pytest.raises(ApiError) as info:
        await create(db, data(status_webhook_url="ftp://x.example.com"))
    assert info.value.code == "INVALID_REQUEST"


async def test_rotate_revokes_old_keys_of_that_kind_only(db: AsyncSession) -> None:
    created = await create(db)
    vendor_id = created.vendor.id

    new_live = await rotate_key(db, vendor_id, KeyKind.LIVE)

    old_live = parse_api_key(created.secrets.live_key)
    old_test = parse_api_key(created.secrets.test_key)
    assert old_live is not None
    assert old_test is not None
    assert await api_keys.find_active_by_prefix(db, old_live.prefix) is None
    assert await api_keys.find_active_by_prefix(db, old_test.prefix) is not None

    new_parsed = parse_api_key(new_live)
    assert new_parsed is not None
    row = await api_keys.find_active_by_prefix(db, new_parsed.prefix)
    assert row is not None
    assert verify_api_key(new_live, row.key_hash)
    assert new_live != created.secrets.live_key


async def test_rotate_unknown_vendor_is_404(db: AsyncSession) -> None:
    with pytest.raises(ApiError) as info:
        await rotate_key(db, uuid.uuid4(), KeyKind.LIVE)
    assert info.value.status_code == 404


async def test_concurrent_creates_for_one_number_have_exactly_one_winner(
    engine: AsyncEngine, clean_db: None
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def attempt(i: int) -> str:
        async with factory() as session:
            try:
                await create(session, data(business_name=f"Vendor {i}"))
                await session.commit()
            except ApiError as exc:
                await session.rollback()
                return exc.code
            return "ok"

    results = await asyncio.gather(*(attempt(i) for i in range(8)))
    assert results.count("ok") == 1
    assert results.count("VENDOR_NUMBER_IN_USE") == 7

    async with factory() as session:
        assert len((await session.scalars(select(Vendor))).all()) == 1
        assert len((await session.scalars(select(ApiKey))).all()) == 2
