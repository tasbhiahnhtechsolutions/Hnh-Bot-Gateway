"""Admin panel pages (plan section 9): server-rendered, no JavaScript, no CDN."""

import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.admin.deps import (
    COOKIE_PATH,
    LOGIN_CSRF_COOKIE,
    SESSION_COOKIE,
    AdminContext,
    AdminForbidden,
    AdminLoginRequired,
    AdminSetupRequired,
    Manager,
    ManagerPost,
    Operator,
    Viewer,
    login_redirect,
)
from app.api.deps import Db, Deps
from app.core import totp
from app.core.errors import ApiError, api_error_handler, http_error_handler
from app.core.logging import get_logger
from app.core.security import KeyKind
from app.models import (
    CannedMatchType,
    CannedRule,
    InboundMode,
    OutboundMode,
    RequestKind,
    Vendor,
    VendorStatus,
)
from app.repositories import admins, api_keys, canned_rules, request_logs, vendors
from app.repositories import outbound as outbound_repo
from app.schemas.vendor import VendorCreate
from app.services import admin_sessions, daily_cap, webhooks
from app.services import api_keys as key_service
from app.services.admins import audit, check_login
from app.services.devices import live_devices
from app.services.job_view import mask_body, mask_number
from app.services.mock_bot import MockScenario
from app.services.request_log import record_request
from app.services.simulate import (
    SimulationReport,
    build_payload,
    check_bot_connection,
    report_to_dict,
    simulate_incoming,
)
from app.services.vendors import EDITABLE_FIELDS, create_vendor, update_vendor

BASE_DIR = Path(__file__).parent
log = get_logger(__name__)
LAB_FILE_LIMIT = 16 * 1024 * 1024
MAX_PASSWORD_CHARS = 1024
NOTICES = {
    "modes_saved": "Modes saved.",
    "status_saved": "Status saved.",
    "key_revoked": "Key revoked.",
    "rule_added": "Rule added.",
    "rule_deleted": "Rule deleted.",
    "cap_saved": "Daily cap saved.",
    "vendor_saved": "Vendor saved.",
    "vendor_deleted": "Vendor deleted.",
    "replayed": "The delivery will be tried again within seconds.",
    "2fa_on": "Two-factor login is on.",
    "2fa_off": "Two-factor login is off.",
    "2fa_required": "Set up two-factor login to continue.",
}
MEDIA_TYPES = ("image", "sticker", "document", "ptt", "audio")

router = APIRouter(prefix="/admin", include_in_schema=False)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _format_dt(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC") if value else "never"


templates.env.filters["dt"] = _format_dt
templates.env.filters["phone"] = lambda digits: f"+{digits}" if digits else ""
templates.env.filters["pretty"] = lambda value: json.dumps(value, indent=2, ensure_ascii=False)


def render(
    request: Request,
    name: str,
    ctx: AdminContext | None,
    *,
    status: int = 200,
    csrf: str | None = None,
    **values: Any,
) -> Response:
    context = {
        "ctx": ctx,
        "admin": ctx.admin if ctx else None,
        "csrf": csrf if csrf is not None else (ctx.csrf_token if ctx else ""),
        "notice": NOTICES.get(request.query_params.get("notice", "")),
        "enable_mock_bot": request.app.state.settings.enable_mock_bot,
        **values,
    }
    return templates.TemplateResponse(request, name, context, status_code=status)


def render_error(
    request: Request, ctx: AdminContext | None, message: str, status: int = 400
) -> Response:
    return render(request, "error.html", ctx, status=status, message=message)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _form(request: Request) -> dict[str, str]:
    """Text fields of a posted form (already CSRF-checked). Files are read separately."""
    form = await request.form(max_files=2, max_fields=50)
    return {k: v.strip() for k, v in form.items() if isinstance(v, str)}


async def _vendor_or_404(db: Db, vendor_id: uuid.UUID) -> Vendor:
    vendor = await vendors.get(db, vendor_id)
    if vendor is None:
        raise ApiError(404, "VENDOR_NOT_FOUND", "no vendor with this id")
    return vendor


def _detail_url(vendor_id: uuid.UUID, notice: str) -> Response:
    return RedirectResponse(f"/admin/vendors/{vendor_id}?notice={notice}", status_code=303)


def _validation_text(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:6])


# --- login / logout --------------------------------------------------------------------------


def _login_page(request: Request, *, error: str | None = None, status: int = 200) -> Response:
    token = secrets.token_urlsafe(32)
    response = render(request, "login.html", None, status=status, csrf=token, error=error)
    response.set_cookie(
        LOGIN_CSRF_COOKIE,
        token,
        max_age=15 * 60,
        httponly=True,
        samesite="strict",
        secure=request.app.state.settings.admin_cookie_secure,
        path=COOKIE_PATH,
    )
    return response


@router.get("/login")
async def login_page(request: Request) -> Response:
    return _login_page(request)


@router.post("/login")
async def login(request: Request, db: Db) -> Response:
    redis = request.app.state.redis
    form = await request.form(max_files=0, max_fields=10)
    email = str(form.get("email") or "").strip()
    password = str(form.get("password") or "")
    form_token = str(form.get("csrf_token") or "")
    cookie_token = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    if not form_token or not hmac.compare_digest(form_token, cookie_token):
        return _login_page(request, error="The form expired. Please try again.", status=403)
    if len(password) > MAX_PASSWORD_CHARS or len(email) > 320:
        return _login_page(request, error="Wrong email or password.", status=401)

    ip = _client_ip(request)
    try:
        wait = await admin_sessions.login_blocked_for(redis, email, ip)
        if wait:
            minutes = max(1, round(wait / 60))
            return _login_page(
                request,
                error=f"Too many failed attempts. Try again in about {minutes} minute(s).",
                status=429,
            )
        admin = await check_login(db, email, password)
        if admin is None:
            await db.rollback()
            await admin_sessions.record_login_failure(redis, email, ip)
            return _login_page(request, error="Wrong email or password.", status=401)
        if admin.totp_secret_enc:
            # Right password, but a code from the authenticator app is still needed.
            pending = await admin_sessions.create_pending_login(redis, admin.id)
            await db.commit()  # keeps a password re-hash; the login itself is not done yet
            return _two_factor_page(request, pending, form_token)
        await admin_sessions.clear_login_failures(redis, email)
        return await _start_session(request, db, admin.id)
    except RedisError:
        return _login_page(request, error="Login is unavailable right now.", status=503)


def _two_factor_page(
    request: Request, pending: str, csrf: str, *, error: str | None = None, status: int = 200
) -> Response:
    return render(
        request, "login_2fa.html", None, status=status, csrf=csrf, pending=pending, error=error
    )


async def _start_session(request: Request, db: Db, admin_id: uuid.UUID) -> Response:
    settings = request.app.state.settings
    session_id, _csrf = await admin_sessions.create_session(
        request.app.state.redis, admin_id, settings.admin_session_hours * 3600
    )
    await audit(db, admin_id, "login")
    await db.commit()
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=settings.admin_session_hours * 3600,
        httponly=True,
        samesite="strict",
        secure=settings.admin_cookie_secure,
        path=COOKIE_PATH,
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE, path=COOKIE_PATH)
    return response


@router.post("/login/2fa")
async def login_two_factor(request: Request, db: Db) -> Response:
    redis = request.app.state.redis
    box = request.app.state.inbound_deps.box
    form = await request.form(max_files=0, max_fields=10)
    form_token = str(form.get("csrf_token") or "")
    cookie_token = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    if not form_token or not hmac.compare_digest(form_token, cookie_token):
        return _login_page(request, error="The form expired. Please try again.", status=403)
    pending = str(form.get("pending") or "")
    code = str(form.get("code") or "")[:12]
    try:
        admin_id = await admin_sessions.peek_pending_login(redis, pending)
        admin = await admins.get(db, admin_id) if admin_id else None
        if admin is None or not admin.is_active or not admin.totp_secret_enc:
            return _login_page(request, error="That login expired. Start again.", status=401)
        ip = _client_ip(request)
        wait = await admin_sessions.login_blocked_for(redis, admin.email, ip)
        if wait:
            return _login_page(
                request, error="Too many failed attempts. Try again later.", status=429
            )
        step = totp.verify(box.decrypt(admin.totp_secret_enc), code)
        if step is None or not await admin_sessions.claim_code_step(redis, admin.id, step):
            await admin_sessions.record_login_failure(redis, admin.email, ip)
            if not await admin_sessions.count_code_attempt(redis, pending):
                return _login_page(request, error="Too many wrong codes. Start again.", status=401)
            return _two_factor_page(
                request, pending, form_token, error="Wrong code. Try the current one.", status=401
            )
        await admin_sessions.finish_pending_login(redis, pending)
        await admin_sessions.clear_login_failures(redis, admin.email)
        return await _start_session(request, db, admin.id)
    except RedisError:
        return _login_page(request, error="Login is unavailable right now.", status=503)


@router.post("/logout")
async def logout(request: Request, ctx: Operator) -> Response:
    await admin_sessions.destroy_session(request.app.state.redis, ctx.session_id)
    response = login_redirect()
    response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH)
    return response


# --- account: two-factor login ---------------------------------------------------------------


async def _account_page(
    request: Request,
    ctx: AdminContext,
    db: Db,
    *,
    error: str | None = None,
    status: int = 200,
) -> Response:
    admin = await admins.get(db, ctx.admin.id)
    enabled = bool(admin and admin.totp_secret_enc)
    secret = None
    if not enabled:
        secret = await admin_sessions.load_setup_secret(request.app.state.redis, ctx.admin.id)
    return render(
        request,
        "account.html",
        ctx,
        status=status,
        error=error,
        enabled=enabled,
        secret=secret,
        uri=totp.otpauth_uri(secret, ctx.admin.email, "Vendor Gateway") if secret else None,
        required=request.app.state.settings.admin_require_2fa,
    )


@router.get("/account")
async def account(request: Request, ctx: Viewer, db: Db) -> Response:
    return await _account_page(request, ctx, db)


@router.post("/account/2fa/start")
async def two_factor_start(request: Request, ctx: Operator, db: Db) -> Response:
    admin = await admins.get(db, ctx.admin.id)
    if admin is not None and admin.totp_secret_enc:
        return await _account_page(
            request, ctx, db, error="Two-factor login is already on.", status=409
        )
    await admin_sessions.save_setup_secret(request.app.state.redis, ctx.admin.id, totp.new_secret())
    return RedirectResponse("/admin/account", status_code=303)


@router.post("/account/2fa/enable")
async def two_factor_enable(request: Request, ctx: Operator, db: Db, deps: Deps) -> Response:
    redis = request.app.state.redis
    values = await _form(request)
    secret = await admin_sessions.load_setup_secret(redis, ctx.admin.id)
    if secret is None:
        return await _account_page(request, ctx, db, error="Start the setup again.", status=400)
    step = totp.verify(secret, values.get("code", ""))
    if step is None or not await admin_sessions.claim_code_step(redis, ctx.admin.id, step):
        return await _account_page(
            request, ctx, db, error="Wrong code. Try the current one.", status=400
        )
    admin = await admins.get(db, ctx.admin.id)
    if admin is None:
        raise AdminLoginRequired
    admin.totp_secret_enc = deps.box.encrypt(secret)
    await audit(db, ctx.admin.id, "admin.2fa_enable")
    await db.commit()
    await admin_sessions.clear_setup_secret(redis, ctx.admin.id)
    return RedirectResponse("/admin/account?notice=2fa_on", status_code=303)


@router.post("/account/2fa/disable")
async def two_factor_disable(request: Request, ctx: Operator, db: Db, deps: Deps) -> Response:
    redis = request.app.state.redis
    values = await _form(request)
    admin = await admins.get(db, ctx.admin.id)
    if admin is None or not admin.totp_secret_enc:
        return await _account_page(
            request, ctx, db, error="Two-factor login is not on.", status=400
        )
    step = totp.verify(deps.box.decrypt(admin.totp_secret_enc), values.get("code", ""))
    if step is None or not await admin_sessions.claim_code_step(redis, ctx.admin.id, step):
        return await _account_page(
            request, ctx, db, error="Wrong code. Try the current one.", status=400
        )
    admin.totp_secret_enc = None
    await audit(db, ctx.admin.id, "admin.2fa_disable")
    await db.commit()
    return RedirectResponse("/admin/account?notice=2fa_off", status_code=303)


# --- dashboard, vendors list -----------------------------------------------------------------


@router.get("")
async def dashboard(request: Request, ctx: Viewer, db: Db) -> Response:
    all_vendors = await vendors.list_all(db)
    since = datetime.now(UTC) - timedelta(hours=24)
    return render(
        request,
        "dashboard.html",
        ctx,
        total=len(all_vendors),
        active=sum(v.status == VendorStatus.ACTIVE.value for v in all_vendors),
        counts=await request_logs.counts_since(db, since),
    )


@router.get("/vendors")
async def vendors_list(request: Request, ctx: Viewer, db: Db) -> Response:
    return render(
        request,
        "vendors.html",
        ctx,
        vendors=await vendors.list_all(db),
        last_seen=await request_logs.last_seen_by_vendor(db),
    )


# --- create vendor ---------------------------------------------------------------------------


def _new_vendor_page(
    request: Request, ctx: AdminContext, values: dict[str, str], error: str | None, status: int
) -> Response:
    return render(
        request,
        "vendor_new.html",
        ctx,
        status=status,
        v=values,
        error=error,
        inbound_modes=list(InboundMode),
        outbound_modes=list(OutboundMode),
    )


@router.get("/vendors/new")
async def vendor_new_page(request: Request, ctx: Manager) -> Response:
    return _new_vendor_page(request, ctx, {}, None, 200)


_CREATE_FIELDS = (
    "business_name",
    "whatsapp_number",
    "email",
    "bot_base_url",
    "status_webhook_url",
    "default_country_code",
    "marketing_daily_cap",
    "inbound_mode",
    "outbound_mode",
    "internal_note",
)


@router.post("/vendors/new")
async def vendor_new(request: Request, ctx: ManagerPost, db: Db, deps: Deps) -> Response:
    values = await _form(request)
    fields = {k: values[k] for k in _CREATE_FIELDS if values.get(k)}
    try:
        data = VendorCreate(**fields)
        if data.inbound_mode is InboundMode.MOCK_BOT and not deps.settings.enable_mock_bot:
            raise ApiError(400, "INVALID_REQUEST", "mock_bot is not enabled in this environment")
        created = await create_vendor(
            db,
            data,
            box=deps.box,
            allow_private_bot_urls=deps.settings.allow_private_bot_urls,
        )
        await audit(db, ctx.admin.id, "vendor.create", f"vendor:{created.vendor.id}")
        await db.commit()
    except ValidationError as exc:
        await db.rollback()
        return _new_vendor_page(request, ctx, values, _validation_text(exc), 400)
    except ApiError as exc:
        await db.rollback()
        return _new_vendor_page(request, ctx, values, exc.message, exc.status_code)
    return render(
        request, "vendor_created.html", ctx, vendor=created.vendor, secrets=created.secrets
    )


# --- vendor detail and changes ---------------------------------------------------------------


async def _detail_page(
    request: Request,
    ctx: AdminContext,
    db: Db,
    vendor: Vendor,
    *,
    error: str | None = None,
    status: int = 200,
) -> Response:
    return render(
        request,
        "vendor_detail.html",
        ctx,
        status=status,
        error=error,
        vendor=vendor,
        keys=await api_keys.list_for_vendor(db, vendor.id),
        devices=await _devices_or_empty(request, db, vendor.id),
        cap_used=await _cap_used(request, vendor.id),
        jobs=_job_rows(await outbound_repo.list_jobs(db, vendor.id, limit=10)),
        rules=await canned_rules.list_for_vendor(db, vendor.id),
        logs=await request_logs.list_recent(db, vendor_id=vendor.id, limit=20),
        last_seen=(await request_logs.last_seen_by_vendor(db)).get(vendor.id),
        inbound_modes=list(InboundMode),
        outbound_modes=list(OutboundMode),
        match_types=list(CannedMatchType),
        media_types=MEDIA_TYPES,
    )


async def _devices_or_empty(request: Request, db: Db, vendor_id: uuid.UUID) -> list[Any]:
    try:
        return await live_devices(db, request.app.state.redis, vendor_id)
    except RedisError:
        return []


async def _cap_used(request: Request, vendor_id: uuid.UUID) -> int | None:
    try:
        return await daily_cap.used_today(request.app.state.redis, vendor_id)
    except RedisError:
        return None


def _job_rows(jobs: list[Any]) -> list[dict[str, Any]]:
    """What the admin may see of a job: masked number, masked text (plan section 9.2)."""
    return [
        {
            "id": j.id,
            "created_at": j.created_at,
            "to": mask_number(j.to_number),
            "type": j.type,
            "status": j.status,
            "attempts": j.attempts,
            "error": j.error_code or "",
            "ack": j.wa_ack,
            "body": mask_body(j.body, j.type),
            "test": j.is_test,
        }
        for j in jobs
    ]


async def _drop_plugin_line(request: Request, vendor_id: uuid.UUID) -> None:
    """Key revoked or vendor suspended: the plugin must not stay connected for a second longer."""
    try:
        await request.app.state.hub.kick_vendor(vendor_id)
    except Exception as exc:  # the database change is already saved; presence expires on its own
        log.warning("kick_failed", error_type=type(exc).__name__)


@router.get("/vendors/{vendor_id}")
async def vendor_detail(request: Request, ctx: Viewer, db: Db, vendor_id: uuid.UUID) -> Response:
    return await _detail_page(request, ctx, db, await _vendor_or_404(db, vendor_id))


# --- edit and delete a vendor ----------------------------------------------------------------


def _edit_vendor_page(
    request: Request,
    ctx: AdminContext,
    vendor: Vendor,
    values: dict[str, str],
    error: str | None,
    status: int,
) -> Response:
    return render(
        request, "vendor_edit.html", ctx, status=status, vendor=vendor, v=values, error=error
    )


def _vendor_values(vendor: Vendor) -> dict[str, str]:
    return {
        "business_name": vendor.business_name,
        "whatsapp_number": vendor.phone_e164,
        "email": vendor.email,
        "bot_base_url": vendor.bot_base_url or "",
        "status_webhook_url": vendor.status_webhook_url or "",
        "default_country_code": vendor.default_country_code,
        "internal_note": vendor.internal_note or "",
    }


@router.get("/vendors/{vendor_id}/edit")
async def vendor_edit_page(
    request: Request, ctx: Manager, db: Db, vendor_id: uuid.UUID
) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    return _edit_vendor_page(request, ctx, vendor, _vendor_values(vendor), None, 200)


@router.post("/vendors/{vendor_id}/edit")
async def vendor_edit(
    request: Request, ctx: ManagerPost, db: Db, deps: Deps, vendor_id: uuid.UUID
) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    values = await _form(request)
    shown = {k: values.get(k, "") for k in EDITABLE_FIELDS}
    try:
        data = VendorCreate(
            **{k: values[k] for k in EDITABLE_FIELDS if values.get(k)},
            # Not editable here: keep what the vendor has (they are not checked or stored).
            inbound_mode=InboundMode(vendor.inbound_mode),
            outbound_mode=OutboundMode(vendor.outbound_mode),
            marketing_daily_cap=vendor.marketing_daily_cap,
        )
        changed = await update_vendor(
            db, vendor, data, allow_private_bot_urls=deps.settings.allow_private_bot_urls
        )
        if changed:
            await audit(
                db, ctx.admin.id, "vendor.edit", f"vendor:{vendor.id} changed: {', '.join(changed)}"
            )
        await db.commit()
    except ValidationError as exc:
        await db.rollback()
        vendor = await _vendor_or_404(db, vendor_id)  # the rollback expired the old object
        return _edit_vendor_page(request, ctx, vendor, shown, _validation_text(exc), 400)
    except ApiError as exc:
        await db.rollback()
        vendor = await _vendor_or_404(db, vendor_id)
        return _edit_vendor_page(request, ctx, vendor, shown, exc.message, exc.status_code)
    if "whatsapp_number" in changed:
        await _drop_plugin_line(request, vendor.id)  # it must reconnect with the new number
    return _detail_url(vendor.id, "vendor_saved")


@router.post("/vendors/{vendor_id}/delete")
async def vendor_delete(
    request: Request, ctx: ManagerPost, db: Db, vendor_id: uuid.UUID
) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    values = await _form(request)
    if values.get("confirm_name") != vendor.business_name:
        return await _detail_page(
            request,
            ctx,
            db,
            vendor,
            error="To delete, type the exact business name of this vendor.",
            status=400,
        )
    name, vendor_pk = vendor.business_name, vendor.id
    await audit(db, ctx.admin.id, "vendor.delete", f"vendor:{vendor_pk} ({name})")
    await vendors.delete(db, vendor)
    await db.commit()
    await _drop_plugin_line(request, vendor_pk)
    return RedirectResponse("/admin/vendors?notice=vendor_deleted", status_code=303)


@router.post("/vendors/{vendor_id}/modes")
async def vendor_modes(
    request: Request, ctx: ManagerPost, db: Db, deps: Deps, vendor_id: uuid.UUID
) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    values = await _form(request)
    try:
        inbound = InboundMode(values.get("inbound_mode", ""))
        outbound = OutboundMode(values.get("outbound_mode", ""))
    except ValueError:
        return await _detail_page(request, ctx, db, vendor, error="Unknown mode.", status=400)
    if inbound is InboundMode.MOCK_BOT and not deps.settings.enable_mock_bot:
        return await _detail_page(
            request, ctx, db, vendor, error="mock_bot is not enabled here.", status=400
        )
    leaving_forward = inbound.value != vendor.inbound_mode and inbound is not InboundMode.FORWARD
    if leaving_forward and values.get("confirm") != "on":
        return await _detail_page(
            request,
            ctx,
            db,
            vendor,
            error="Tick the box to confirm: customers will get non-bot replies or silence.",
            status=400,
        )
    changes = []
    if inbound.value != vendor.inbound_mode:
        changes.append(f"inbound {vendor.inbound_mode}->{inbound.value}")
    if outbound.value != vendor.outbound_mode:
        changes.append(f"outbound {vendor.outbound_mode}->{outbound.value}")
    if changes:
        vendor.inbound_mode = inbound.value
        vendor.outbound_mode = outbound.value
        await audit(
            db, ctx.admin.id, "vendor.mode_change", f"vendor:{vendor.id} {', '.join(changes)}"
        )
        await db.commit()
    return _detail_url(vendor.id, "modes_saved")


@router.post("/vendors/{vendor_id}/status")
async def vendor_status(
    request: Request, ctx: ManagerPost, db: Db, vendor_id: uuid.UUID
) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    values = await _form(request)
    action = values.get("action")
    if action == "suspend":
        if values.get("confirm") != "on":
            return await _detail_page(
                request,
                ctx,
                db,
                vendor,
                error="Tick the box to confirm the suspension.",
                status=400,
            )
        new_status = VendorStatus.SUSPENDED
    elif action == "activate":
        other = await vendors.get_active_by_phone(db, vendor.phone_e164)
        if other is not None and other.id != vendor.id:
            return await _detail_page(
                request,
                ctx,
                db,
                vendor,
                error="Another active vendor already has this number.",
                status=409,
            )
        new_status = VendorStatus.ACTIVE
    else:
        return await _detail_page(request, ctx, db, vendor, error="Unknown action.", status=400)
    vendor.status = new_status.value
    cancelled = 0
    if new_status is VendorStatus.SUSPENDED:
        cancelled = await outbound_repo.cancel_queued(db, vendor.id)
    detail = f" ({cancelled} queued messages cancelled)" if cancelled else ""
    await audit(
        db,
        ctx.admin.id,
        "vendor.status_change",
        f"vendor:{vendor.id} -> {new_status.value}{detail}",
    )
    try:
        await db.commit()
        if new_status is VendorStatus.SUSPENDED:
            await _drop_plugin_line(request, vendor.id)
    except IntegrityError:
        await db.rollback()
        return await _detail_page(
            request,
            ctx,
            db,
            vendor,
            error="Another active vendor already has this number.",
            status=409,
        )
    return _detail_url(vendor.id, "status_saved")


@router.post("/vendors/{vendor_id}/keys/rotate")
async def key_rotate(request: Request, ctx: ManagerPost, db: Db, vendor_id: uuid.UUID) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    values = await _form(request)
    try:
        kind = KeyKind(values.get("kind", ""))
    except ValueError:
        return await _detail_page(request, ctx, db, vendor, error="Unknown key kind.", status=400)
    new_key = await key_service.rotate_key(db, vendor.id, kind)
    await audit(db, ctx.admin.id, "key.rotate", f"vendor:{vendor.id} {kind.value}")
    await db.commit()
    if kind is KeyKind.LIVE:
        await _drop_plugin_line(request, vendor.id)  # it holds the old key: it must sign in again
    return render(request, "key_rotated.html", ctx, vendor=vendor, kind=kind.value, new_key=new_key)


@router.post("/vendors/{vendor_id}/keys/{key_id}/revoke")
async def key_revoke(
    request: Request, ctx: ManagerPost, db: Db, vendor_id: uuid.UUID, key_id: uuid.UUID
) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    if key_id not in {k.id for k in await api_keys.list_for_vendor(db, vendor.id)}:
        return await _detail_page(request, ctx, db, vendor, error="Key not found.", status=404)
    keys = await api_keys.list_for_vendor(db, vendor.id)
    revoked_kind = next(k.kind for k in keys if k.id == key_id)
    if await api_keys.revoke(db, key_id):
        cancelled = 0
        if revoked_kind == KeyKind.LIVE.value:
            still_active = any(
                k.kind == KeyKind.LIVE.value and k.status == "active" and k.id != key_id
                for k in keys
            )
            if (
                not still_active
            ):  # nobody can send for this vendor any more: do not leave jobs rotting
                cancelled = await outbound_repo.cancel_queued(db, vendor.id)
        detail = f" ({cancelled} queued messages cancelled)" if cancelled else ""
        await audit(db, ctx.admin.id, "key.revoke", f"vendor:{vendor.id} key:{key_id}{detail}")
        await db.commit()
        if revoked_kind == KeyKind.LIVE.value:
            await _drop_plugin_line(request, vendor.id)
    return _detail_url(vendor.id, "key_revoked")


@router.post("/vendors/{vendor_id}/rules")
async def rule_add(request: Request, ctx: ManagerPost, db: Db, vendor_id: uuid.UUID) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    values = await _form(request)
    error: str | None = None
    try:
        match_type = CannedMatchType(values.get("match_type", ""))
        priority = int(values.get("priority") or 100)
    except ValueError:
        return await _detail_page(request, ctx, db, vendor, error="Invalid rule.", status=400)
    match_value = values.get("match_value", "")
    reply = values.get("reply_text", "")
    if not 1 <= len(reply) <= 4000:
        error = "The reply must be 1 to 4000 characters."
    elif match_type is CannedMatchType.DEFAULT:
        match_value = ""
    elif not match_value:
        error = "Give a word/phrase or a media type."
    elif match_type is CannedMatchType.MEDIA_TYPE and match_value not in MEDIA_TYPES:
        error = f"media type must be one of {', '.join(MEDIA_TYPES)}"
    if error:
        return await _detail_page(request, ctx, db, vendor, error=error, status=400)
    await canned_rules.add(
        db,
        CannedRule(
            vendor_id=vendor.id,
            priority=priority,
            match_type=match_type.value,
            match_value=match_value,
            reply_text=reply,
        ),
    )
    await audit(db, ctx.admin.id, "canned_rule.add", f"vendor:{vendor.id}")
    await db.commit()
    return _detail_url(vendor.id, "rule_added")


@router.post("/vendors/{vendor_id}/rules/{rule_id}/delete")
async def rule_delete(
    request: Request, ctx: ManagerPost, db: Db, vendor_id: uuid.UUID, rule_id: uuid.UUID
) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    if await canned_rules.delete_for_vendor(db, vendor.id, rule_id):
        await audit(db, ctx.admin.id, "canned_rule.delete", f"vendor:{vendor.id}")
        await db.commit()
    return _detail_url(vendor.id, "rule_deleted")


# --- daily cap, devices, jobs, webhook deliveries (Phase 2) -----------------------------------

MAX_DAILY_CAP = 100_000


@router.post("/vendors/{vendor_id}/cap")
async def vendor_cap(request: Request, ctx: ManagerPost, db: Db, vendor_id: uuid.UUID) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    values = await _form(request)
    raw = values.get("marketing_daily_cap", "")
    if not raw.isdigit() or not 0 <= int(raw) <= MAX_DAILY_CAP:
        return await _detail_page(
            request,
            ctx,
            db,
            vendor,
            error=f"The daily cap must be a whole number from 0 to {MAX_DAILY_CAP}.",
            status=400,
        )
    new_cap = int(raw)
    if new_cap != vendor.marketing_daily_cap:
        await audit(
            db,
            ctx.admin.id,
            "vendor.cap_change",
            f"vendor:{vendor.id} cap {vendor.marketing_daily_cap}->{new_cap}",
        )
        vendor.marketing_daily_cap = new_cap
        await db.commit()
    return _detail_url(vendor.id, "cap_saved")


@router.get("/vendors/{vendor_id}/devices")
async def vendor_devices(request: Request, ctx: Viewer, db: Db, vendor_id: uuid.UUID) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    return render(
        request,
        "devices.html",
        ctx,
        vendor=vendor,
        devices=await _devices_or_empty(request, db, vendor.id),
    )


JOB_STATUSES = (
    "queued",
    "dispatched",
    "sending",
    "sent",
    "failed",
    "expired",
    "unknown",
    "cancelled",
)


@router.get("/vendors/{vendor_id}/jobs")
async def vendor_jobs(request: Request, ctx: Viewer, db: Db, vendor_id: uuid.UUID) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    status = request.query_params.get("status") or None
    if status is not None and status not in JOB_STATUSES:
        return render_error(request, ctx, "Unknown status.")
    return render(
        request,
        "jobs.html",
        ctx,
        vendor=vendor,
        jobs=_job_rows(await outbound_repo.list_jobs(db, vendor.id, status=status, limit=100)),
        statuses=JOB_STATUSES,
        selected=status,
    )


@router.get("/vendors/{vendor_id}/webhooks")
async def vendor_webhooks(request: Request, ctx: Viewer, db: Db, vendor_id: uuid.UUID) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    return render(
        request,
        "webhooks.html",
        ctx,
        vendor=vendor,
        deliveries=await outbound_repo.list_deliveries(db, vendor.id, limit=100),
    )


@router.post("/vendors/{vendor_id}/webhooks/{delivery_id}/replay")
async def webhook_replay(
    request: Request, ctx: ManagerPost, db: Db, deps: Deps, vendor_id: uuid.UUID, delivery_id: int
) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    if not await webhooks.replay(db, delivery_id, vendor.id):
        return render_error(
            request, ctx, "That delivery is not there, or it is still being tried.", 409
        )
    await audit(db, ctx.admin.id, "webhook.replay", f"vendor:{vendor.id} delivery:{delivery_id}")
    await db.commit()
    try:
        await request.app.state.runtime.scheduler.deliver_webhook(delivery_id)
    except Exception as exc:  # the sweeper finds it within 30 seconds anyway
        log.warning("replay_schedule_failed", error_type=type(exc).__name__)
    return RedirectResponse(f"/admin/vendors/{vendor.id}/webhooks?notice=replayed", status_code=303)


# --- Test Lab --------------------------------------------------------------------------------


def _lab_result(
    request: Request, ctx: AdminContext, vendor: Vendor, title: str, report: SimulationReport
) -> Response:
    data = report_to_dict(report)
    return render(request, "lab_result.html", ctx, vendor=vendor, title=title, report=data)


@router.get("/vendors/{vendor_id}/lab")
async def lab_page(request: Request, ctx: Viewer, db: Db, vendor_id: uuid.UUID) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    return render(
        request,
        "lab.html",
        ctx,
        vendor=vendor,
        error=None,
        media_types=MEDIA_TYPES,
        scenarios=[s.value for s in MockScenario],
    )


@router.post("/vendors/{vendor_id}/lab/simulate")
async def lab_simulate(
    request: Request, ctx: Operator, db: Db, deps: Deps, vendor_id: uuid.UUID
) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    form = await request.form(max_files=2, max_fields=50)

    def lab_error(message: str, status: int = 400) -> Response:
        return render(
            request,
            "lab.html",
            ctx,
            status=status,
            vendor=vendor,
            error=message,
            media_types=MEDIA_TYPES,
            scenarios=[s.value for s in MockScenario],
        )

    text = str(form.get("text") or "").strip()
    upload = form.get("file")
    file_bytes: bytes | None = None
    file_name: str | None = None
    if upload is not None and not isinstance(upload, str) and upload.filename:
        file_bytes = await upload.read(LAB_FILE_LIMIT + 1)
        if len(file_bytes) > LAB_FILE_LIMIT:
            return lab_error("The file is larger than 16 MB.", 413)
        file_name = upload.filename
    if not text and file_bytes is None:
        return lab_error("Write a message and/or attach a file.")
    media_type = str(form.get("media_type") or "") or None
    try:
        scenario = MockScenario(str(form.get("scenario") or MockScenario.OK.value))
        payload = build_payload(
            text=text, file_name=file_name, file_bytes=file_bytes, media_type=media_type
        )
    except (ValueError, ApiError) as exc:
        return lab_error(exc.message if isinstance(exc, ApiError) else "Unknown scenario.")

    use_real_bot = form.get("use_real_bot") == "on"
    request_id = f"lab-{uuid.uuid4()}"
    started = time.monotonic()
    report = await simulate_incoming(
        db,
        deps,
        vendor,
        payload,
        scenario=scenario,
        mode_override=InboundMode.FORWARD if use_real_bot else None,
        request_id=request_id,
    )
    await record_request(
        db,
        vendor_id=vendor.id,
        request_id=request_id,
        kind=RequestKind.LAB,
        mode=report.mode,
        latency_ms=round((time.monotonic() - started) * 1000),
        error_code=report.error_code,
    )
    return _lab_result(request, ctx, vendor, "Simulation result", report)


@router.post("/vendors/{vendor_id}/lab/test-bot")
async def lab_test_bot(
    request: Request, ctx: Operator, db: Db, deps: Deps, vendor_id: uuid.UUID
) -> Response:
    vendor = await _vendor_or_404(db, vendor_id)
    request_id = f"test-{uuid.uuid4()}"
    started = time.monotonic()
    report = await check_bot_connection(db, deps, vendor, request_id=request_id)
    await record_request(
        db,
        vendor_id=vendor.id,
        request_id=request_id,
        kind=RequestKind.BOT_TEST,
        mode=report.mode,
        latency_ms=round((time.monotonic() - started) * 1000),
        error_code=report.error_code,
    )
    return _lab_result(request, ctx, vendor, "Bot connection test", report)


# --- logs and audit --------------------------------------------------------------------------


@router.get("/logs")
async def logs_page(request: Request, ctx: Viewer, db: Db) -> Response:
    raw = request.query_params.get("vendor_id")
    vendor_id: uuid.UUID | None = None
    if raw:
        try:
            vendor_id = uuid.UUID(raw)
        except ValueError:
            return render_error(request, ctx, "Invalid vendor id.")
    all_vendors = await vendors.list_all(db)
    return render(
        request,
        "logs.html",
        ctx,
        logs=await request_logs.list_recent(db, vendor_id=vendor_id, limit=100),
        names={v.id: v.business_name for v in all_vendors},
        vendors=all_vendors,
        selected=vendor_id,
    )


@router.get("/audit")
async def audit_page(request: Request, ctx: Manager, db: Db) -> Response:
    return render(request, "audit.html", ctx, entries=await admins.list_audit(db))


# --- wiring ----------------------------------------------------------------------------------


def register_admin(app: FastAPI) -> None:
    app.include_router(router)
    app.mount("/admin/static", StaticFiles(directory=str(BASE_DIR / "static")), name="admin-static")

    async def _login_required(_request: Request, _exc: Exception) -> Response:
        return login_redirect()

    async def _forbidden(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, AdminForbidden)
        return render_error(request, None, exc.message, 403)

    async def _api_error(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, ApiError)
        if not request.url.path.startswith("/admin"):
            return api_error_handler(request, exc)
        return render_error(request, None, exc.message, exc.status_code)

    async def _http_error(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, StarletteHTTPException)
        if not request.url.path.startswith("/admin"):
            return http_error_handler(request, exc)
        return render_error(request, None, "Page not found.", exc.status_code)

    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)

    async def _setup_required(_request: Request, _exc: Exception) -> Response:
        return RedirectResponse("/admin/account?notice=2fa_required", status_code=303)

    app.add_exception_handler(AdminLoginRequired, _login_required)
    app.add_exception_handler(AdminSetupRequired, _setup_required)
    app.add_exception_handler(AdminForbidden, _forbidden)
