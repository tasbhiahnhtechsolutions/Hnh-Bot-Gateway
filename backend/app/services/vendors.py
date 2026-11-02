"""Vendor onboarding (plan section 9.1). Callers own the transaction (commit)."""

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.security import KeyKind, SecretBox, generate_signing_secret
from app.models import Vendor
from app.repositories import vendors
from app.schemas.vendor import VendorCreate
from app.services.api_keys import issue_key
from app.services.bot_url import Resolver, resolve_host, validate_bot_url


@dataclass(frozen=True, slots=True)
class IssuedSecrets:
    """Shown to the admin exactly once. Never logged, never stored in plain text."""

    live_key: str
    test_key: str
    bot_secret: str
    webhook_secret: str


@dataclass(frozen=True, slots=True)
class CreatedVendor:
    vendor: Vendor
    secrets: IssuedSecrets


async def create_vendor(
    db: AsyncSession,
    data: VendorCreate,
    *,
    box: SecretBox,
    allow_private_bot_urls: bool,
    resolver: Resolver = resolve_host,
) -> CreatedVendor:
    await validate_bot_url(
        data.bot_base_url, allow_private=allow_private_bot_urls, resolver=resolver
    )
    if data.status_webhook_url:
        await validate_bot_url(
            data.status_webhook_url, allow_private=allow_private_bot_urls, resolver=resolver
        )

    number_in_use = ApiError(
        409, "VENDOR_NUMBER_IN_USE", "an active vendor already has this number"
    )
    if await vendors.get_active_by_phone(db, data.whatsapp_number) is not None:
        raise number_in_use

    bot_secret = generate_signing_secret()
    webhook_secret = generate_signing_secret()
    vendor = Vendor(
        business_name=data.business_name,
        phone_e164=data.whatsapp_number,
        email=str(data.email),
        bot_base_url=data.bot_base_url.rstrip("/"),
        bot_secret_enc=box.encrypt(bot_secret),
        status_webhook_url=data.status_webhook_url,
        webhook_secret_enc=box.encrypt(webhook_secret),
        default_country_code=data.default_country_code,
        marketing_daily_cap=data.marketing_daily_cap,
        inbound_mode=data.inbound_mode.value,
        outbound_mode=data.outbound_mode.value,
        internal_note=data.internal_note,
    )
    try:
        await vendors.add(db, vendor)
    except IntegrityError as exc:
        # Lost a race with another create for the same number: the partial unique index caught it.
        raise number_in_use from exc

    secrets = IssuedSecrets(
        live_key=await issue_key(db, vendor.id, KeyKind.LIVE),
        test_key=await issue_key(db, vendor.id, KeyKind.TEST),
        bot_secret=bot_secret,
        webhook_secret=webhook_secret,
    )
    return CreatedVendor(vendor=vendor, secrets=secrets)
