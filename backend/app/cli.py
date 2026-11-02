"""Command line tools: ``gateway create-vendor | list-vendors | rotate-key | seed``.

Secrets (keys, bot secret) are printed to the terminal once and are never logged.
``simulate-incoming`` is added together with the ``/v1/ai-reply`` pipeline it calls (plan step 3).
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.security import KeyKind, SecretBox
from app.db.session import create_engine, create_session_factory
from app.models import InboundMode, OutboundMode
from app.repositories import api_keys, vendors
from app.schemas.vendor import VendorCreate
from app.services import api_keys as key_service
from app.services.seed import seed_demo_vendors
from app.services.vendors import CreatedVendor, create_vendor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gateway", description="Vendor Gateway admin tools")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-vendor", help="onboard a vendor and print its keys once")
    create.add_argument("--name", required=True, dest="business_name")
    create.add_argument("--number", required=True, dest="whatsapp_number")
    create.add_argument("--email", required=True)
    create.add_argument("--bot-url", required=True, dest="bot_base_url")
    create.add_argument("--webhook-url", dest="status_webhook_url")
    create.add_argument("--country-code", default="92", dest="default_country_code")
    create.add_argument("--cap", type=int, default=200, dest="marketing_daily_cap")
    create.add_argument(
        "--inbound-mode",
        choices=[m.value for m in InboundMode],
        default=InboundMode.FORWARD.value,
    )
    create.add_argument(
        "--outbound-mode",
        choices=[m.value for m in OutboundMode],
        default=OutboundMode.REAL_PLUGIN.value,
    )
    create.add_argument("--note", dest="internal_note")

    sub.add_parser("list-vendors", help="list vendors and their keys (no secrets)")

    rotate = sub.add_parser("rotate-key", help="revoke active keys of a kind, issue a new one")
    rotate.add_argument("vendor_id", type=uuid.UUID)
    rotate.add_argument("--kind", choices=[k.value for k in KeyKind], default=KeyKind.LIVE.value)

    sub.add_parser("seed", help="create the 3 demo vendors (dev/test only, safe to repeat)")
    return parser


def _print_secrets(created: CreatedVendor) -> None:
    s = created.secrets
    print(f"Vendor created: {created.vendor.business_name}  id={created.vendor.id}")
    print("These values are shown ONCE and cannot be recovered:")
    print(f"  LIVE key:       {s.live_key}")
    print(f"  TEST key:       {s.test_key}")
    print(f"  Bot secret:     {s.bot_secret}")
    print(f"  Webhook secret: {s.webhook_secret}")


async def run(argv: Sequence[str], settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = settings or get_settings()
    box = SecretBox(settings.secret_encryption_key.get_secret_value())
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as db:
            try:
                await _dispatch(args, db, box, settings)
                await db.commit()
            except ApiError as exc:
                await db.rollback()
                print(f"error: {exc.code}: {exc.message}", file=sys.stderr)
                return 1
            except ValidationError as exc:
                await db.rollback()
                for err in exc.errors():
                    where = ".".join(str(p) for p in err["loc"])
                    print(f"error: {where}: {err['msg']}", file=sys.stderr)
                return 2
    finally:
        await engine.dispose()
    return 0


async def _dispatch(
    args: argparse.Namespace, db: AsyncSession, box: SecretBox, settings: Settings
) -> None:
    allow_private = settings.allow_private_bot_urls
    if args.command == "create-vendor":
        fields = {k: v for k, v in vars(args).items() if k != "command" and v is not None}
        created = await create_vendor(
            db, VendorCreate(**fields), box=box, allow_private_bot_urls=allow_private
        )
        _print_secrets(created)
    elif args.command == "list-vendors":
        for vendor in await vendors.list_all(db):
            keys = await api_keys.list_for_vendor(db, vendor.id)
            active = ", ".join(f"{k.kind}:{k.key_prefix}" for k in keys if k.status == "active")
            print(
                f"{vendor.id}  {vendor.business_name:<20} +{vendor.phone_e164:<14} "
                f"{vendor.status:<9} inbound={vendor.inbound_mode:<8} keys=[{active}]"
            )
    elif args.command == "rotate-key":
        full_key = await key_service.rotate_key(db, args.vendor_id, KeyKind(args.kind))
        print(f"New {args.kind} key (shown ONCE; the previous {args.kind} keys are revoked):")
        print(f"  {full_key}")
    elif args.command == "seed":
        result = await seed_demo_vendors(db, box=box, allow_private_bot_urls=allow_private)
        for created in result.created:
            _print_secrets(created)
            print()
        for name in result.skipped:
            print(f"{name}: already exists (use rotate-key for a new key)")


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
