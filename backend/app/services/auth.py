"""API key authentication (plan section 4)."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.security import KeyKind, parse_api_key, verify_api_key
from app.models import ApiKey, Vendor, VendorStatus
from app.repositories import api_keys, vendors


@dataclass(frozen=True, slots=True)
class AuthContext:
    vendor: Vendor
    key: ApiKey

    @property
    def is_test(self) -> bool:
        return self.key.kind == KeyKind.TEST.value


def _invalid() -> ApiError:
    # One answer for "missing", "malformed", "unknown" and "wrong": nothing to learn from it.
    return ApiError(401, "INVALID_KEY", "API key is missing or not valid")


async def authenticate(db: AsyncSession, raw_key: str | None) -> AuthContext:
    parsed = parse_api_key(raw_key) if raw_key else None
    if raw_key is None or parsed is None:
        raise _invalid()
    row = await api_keys.find_active_by_prefix(db, parsed.prefix)
    if row is None or row.kind != parsed.kind.value or not verify_api_key(raw_key, row.key_hash):
        raise _invalid()
    vendor = await vendors.get(db, row.vendor_id)
    if vendor is None:  # cannot happen (foreign key), but never trust a key without a vendor
        raise _invalid()
    if vendor.status != VendorStatus.ACTIVE.value:
        raise ApiError(403, "VENDOR_SUSPENDED", "this vendor account is suspended")
    return AuthContext(vendor=vendor, key=row)
