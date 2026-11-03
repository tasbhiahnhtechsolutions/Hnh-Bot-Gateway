"""Admin authentication, CSRF and role checks as FastAPI dependencies."""

import hmac
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.admin import AdminRole
from app.repositories import admins
from app.services import admin_sessions

SESSION_COOKIE = "gw_admin_session"
LOGIN_CSRF_COOKIE = "gw_admin_login_csrf"
COOKIE_PATH = "/admin"


class AdminLoginRequired(Exception):
    """Not logged in: the exception handler redirects to the login page."""


class AdminSetupRequired(Exception):
    """ADMIN_REQUIRE_2FA is on and this admin has not set up two-factor login yet."""


class AdminForbidden(Exception):
    """Logged in but the CSRF token or the role does not allow this."""

    def __init__(self, message: str = "You are not allowed to do this.") -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class AdminInfo:
    """Plain copy of the logged-in admin, so a later rollback cannot expire it mid-render."""

    id: uuid.UUID
    email: str
    role: str


@dataclass(frozen=True, slots=True)
class AdminContext:
    admin: AdminInfo
    session_id: str
    csrf_token: str

    @property
    def is_superadmin(self) -> bool:
        return self.admin.role == AdminRole.SUPERADMIN.value


def login_redirect() -> Response:
    return RedirectResponse("/admin/login", status_code=303)


async def get_admin(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> AdminContext:
    session_id = request.cookies.get(SESSION_COOKIE)
    session = await admin_sessions.load_session(request.app.state.redis, session_id)
    if session is None or session_id is None:
        raise AdminLoginRequired
    admin = await admins.get(db, session.admin_id)
    if admin is None or not admin.is_active:
        await admin_sessions.destroy_session(request.app.state.redis, session_id)
        raise AdminLoginRequired
    path = request.url.path
    if (
        request.app.state.settings.admin_require_2fa
        and not admin.totp_secret_enc
        and not path.startswith("/admin/account")
        and path != "/admin/logout"
    ):
        raise AdminSetupRequired
    info = AdminInfo(id=admin.id, email=admin.email, role=admin.role)
    return AdminContext(admin=info, session_id=session_id, csrf_token=session.csrf_token)


async def get_admin_for_post(
    request: Request, ctx: Annotated[AdminContext, Depends(get_admin)]
) -> AdminContext:
    """Logged in and the form carried the session's CSRF token."""
    form = await request.form(max_files=2, max_fields=50)
    token = form.get("csrf_token")
    if not isinstance(token, str) or not hmac.compare_digest(token, ctx.csrf_token):
        raise AdminForbidden("The form expired. Go back, reload the page and try again.")
    return ctx


def _require_superadmin(ctx: AdminContext) -> AdminContext:
    if not ctx.is_superadmin:
        raise AdminForbidden("Only a superadmin can do this.")
    return ctx


async def get_superadmin(ctx: Annotated[AdminContext, Depends(get_admin)]) -> AdminContext:
    return _require_superadmin(ctx)


async def get_superadmin_for_post(
    ctx: Annotated[AdminContext, Depends(get_admin_for_post)],
) -> AdminContext:
    return _require_superadmin(ctx)


Viewer = Annotated[AdminContext, Depends(get_admin)]  # any logged-in admin, GET
Operator = Annotated[AdminContext, Depends(get_admin_for_post)]  # any admin, POST + CSRF
Manager = Annotated[AdminContext, Depends(get_superadmin)]  # superadmin, GET
ManagerPost = Annotated[AdminContext, Depends(get_superadmin_for_post)]  # superadmin, POST + CSRF
