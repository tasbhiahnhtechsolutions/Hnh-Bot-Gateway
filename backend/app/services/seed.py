"""Demo vendors for local development (plan section 3.2: ``make seed``)."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.security import SecretBox
from app.models import CannedMatchType, CannedRule, InboundMode
from app.repositories import canned_rules, vendors
from app.schemas.vendor import VendorCreate
from app.services.vendors import CreatedVendor, create_vendor

MOCK_BOT_URL = "http://localhost:9000"

DEMO_VENDORS: tuple[VendorCreate, ...] = (
    VendorCreate(
        business_name="demo-canned",
        whatsapp_number="923009990001",
        email="demo-canned@example.com",
        bot_base_url=MOCK_BOT_URL,
        inbound_mode=InboundMode.CANNED,
        internal_note="seed: hardcoded replies, no bot call",
    ),
    VendorCreate(
        business_name="demo-echo",
        whatsapp_number="923009990002",
        email="demo-echo@example.com",
        bot_base_url=MOCK_BOT_URL,
        inbound_mode=InboundMode.ECHO,
        internal_note="seed: echoes the incoming message",
    ),
    VendorCreate(
        business_name="demo-mockbot",
        whatsapp_number="923009990003",
        email="demo-mockbot@example.com",
        bot_base_url=MOCK_BOT_URL,
        inbound_mode=InboundMode.MOCK_BOT,
        internal_note="seed: built-in contract-following fake bot",
    ),
)


# Plan section 7.1 example rules, so ``demo-canned`` answers something sensible out of the box.
CANNED_DEMO_RULES: tuple[tuple[int, CannedMatchType, str, str], ...] = (
    (10, CannedMatchType.TEXT_CONTAINS, "price", "Price 85,000 se shuru hota hai."),
    (20, CannedMatchType.MEDIA_TYPE, "image", "Photo mil gayi, jaldi dekhte hain."),
    (30, CannedMatchType.MEDIA_TYPE, "ptt", "Voice note mila, text mein likh dein?"),
    (1000, CannedMatchType.DEFAULT, "", "Shukriya! Hum jald jawab denge."),
)


@dataclass(frozen=True, slots=True)
class SeedResult:
    created: list[CreatedVendor]
    skipped: list[str]  # business names that already existed


async def seed_demo_vendors(
    db: AsyncSession, *, box: SecretBox, allow_private_bot_urls: bool
) -> SeedResult:
    """Create the demo vendors that do not exist yet. Safe to run repeatedly."""
    if not allow_private_bot_urls:
        raise ApiError(
            400, "SEED_NOT_ALLOWED", "seed only runs where ALLOW_PRIVATE_BOT_URLS=true (dev/test)"
        )
    created: list[CreatedVendor] = []
    skipped: list[str] = []
    for spec in DEMO_VENDORS:
        if await vendors.get_by_business_name(db, spec.business_name) is not None:
            skipped.append(spec.business_name)
            continue
        made = await create_vendor(db, spec, box=box, allow_private_bot_urls=allow_private_bot_urls)
        if spec.inbound_mode is InboundMode.CANNED:
            for priority, match_type, value, reply in CANNED_DEMO_RULES:
                rule = CannedRule(
                    vendor_id=made.vendor.id,
                    priority=priority,
                    match_type=match_type.value,
                    match_value=value,
                    reply_text=reply,
                )
                await canned_rules.add(db, rule)
        created.append(made)
    return SeedResult(created=created, skipped=skipped)
