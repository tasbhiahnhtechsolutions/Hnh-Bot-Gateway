"""Admin accounts, login check and audit trail. Callers own the transaction (commit)."""

import uuid
from datetime import UTC, datetime

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.passwords import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models.admin import Admin, AdminRole, AuditLog
from app.repositories import admins


def normalize_email(raw: str) -> str:
    try:
        return validate_email(raw.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise ApiError(400, "INVALID_REQUEST", f"email: {exc}") from exc


async def create_admin(db: AsyncSession, *, email: str, password: str, role: AdminRole) -> Admin:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ApiError(
            400, "INVALID_REQUEST", f"password must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    exists = ApiError(409, "ADMIN_EXISTS", "an admin with this email already exists")
    normalized = normalize_email(email)
    if await admins.get_by_email(db, normalized) is not None:
        raise exists
    try:
        return await admins.add(
            db,
            Admin(email=normalized, password_hash=hash_password(password), role=role.value),
        )
    except IntegrityError as exc:
        raise exists from exc


async def check_login(db: AsyncSession, email: str, password: str) -> Admin | None:
    """The admin if e-mail and password are right and the account is active, else None.

    The answer never says which part was wrong.
    """
    admin = await admins.get_by_email(db, email)
    password_ok = verify_password(admin.password_hash if admin else None, password)
    if admin is None or not password_ok or not admin.is_active:
        return None
    if needs_rehash(admin.password_hash):
        admin.password_hash = hash_password(password)
    admin.last_login_at = datetime.now(UTC)
    return admin


async def audit(
    db: AsyncSession, admin_id: uuid.UUID | None, action: str, target: str | None = None
) -> None:
    """Record a sensitive action. Never pass keys, secrets, passwords or message text."""
    await admins.add_audit(
        db, AuditLog(admin_id=admin_id, action=action[:50], target=target[:300] if target else None)
    )
