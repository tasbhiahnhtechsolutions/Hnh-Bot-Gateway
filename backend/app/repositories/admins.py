import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import Admin, AuditLog


async def add(db: AsyncSession, admin: Admin) -> Admin:
    db.add(admin)
    await db.flush()
    return admin


async def get(db: AsyncSession, admin_id: uuid.UUID) -> Admin | None:
    return await db.get(Admin, admin_id)


async def get_by_email(db: AsyncSession, email: str) -> Admin | None:
    return await db.scalar(select(Admin).where(Admin.email == email.strip().lower()))


async def add_audit(db: AsyncSession, row: AuditLog) -> AuditLog:
    db.add(row)
    await db.flush()
    return row


async def list_audit(db: AsyncSession, limit: int = 200) -> list[tuple[AuditLog, str | None]]:
    rows = await db.execute(
        select(AuditLog, Admin.email)
        .outerjoin(Admin, Admin.id == AuditLog.admin_id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    )
    return [(audit, email) for audit, email in rows.all()]
