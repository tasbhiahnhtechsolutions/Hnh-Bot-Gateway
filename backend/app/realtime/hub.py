"""Socket.IO hub: the open phone line between the gateway and each plugin (plan section 8.4).

One vendor key = one active session (``presence``). Jobs reach a plugin only through the room
``vendor:<id>`` of its own key, so a job of vendor B can never reach plugin A (section 8.9).
"""

import re
import time
import uuid
from typing import Any, NoReturn

import socketio
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.errors import ApiError
from app.core.logging import get_logger
from app.models import Vendor
from app.models.outbound import Device
from app.realtime.emitter import vendor_room
from app.services import jobs, presence, rate_limit
from app.services.auth import authenticate
from app.services.job_results import Outcome, apply_result, apply_started
from app.services.runtime import Runtime
from app.services.wa_number import digits_only

log = get_logger(__name__)

_DEVICE_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")
_JOB_ID = re.compile(r"job_[0-9a-f]{16}")
_VERSION = re.compile(r"[A-Za-z0-9._+-]{1,32}")


class AckPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    jobId: str = Field(pattern=_JOB_ID.pattern)
    attempt: int = Field(ge=0, le=1000)
    stage: str = "started"


class ResultPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    jobId: str = Field(pattern=_JOB_ID.pattern)
    attempt: int = Field(ge=0, le=1000)
    status: str = Field(pattern=r"^(sent|failed|expired)$")
    waAck: int | None = Field(default=None, ge=0, le=5)
    errorCode: str | None = Field(default=None, max_length=40)


class HeartbeatPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    waState: str | None = Field(default=None, max_length=20)
    queueLength: int | None = Field(default=None, ge=0, le=100000)
    sendingJobId: str | None = Field(default=None, max_length=40)


class Hub:
    """Owns the Socket.IO server. ``app.state`` is read at call time so tests can swap parts."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        settings = app.state.settings
        origins = [o.strip() for o in settings.socket_allowed_origins.split(",") if o.strip()]
        self.sio = socketio.AsyncServer(
            async_mode="asgi",
            client_manager=socketio.AsyncRedisManager(settings.redis_url),
            ping_interval=20,
            ping_timeout=15,
            # An extension connects with Origin chrome-extension://<id>. The API key, not the
            # Origin, protects the line (no cookies are used), so "*" is the safe default.
            cors_allowed_origins=origins or "*",
            max_http_buffer_size=64 * 1024,
            logger=False,
            engineio_logger=False,
        )
        self.sio.on("connect", self._on_connect)
        self.sio.on("disconnect", self._on_disconnect)
        self.sio.on("job_ack", self._on_job_ack)
        self.sio.on("job_result", self._on_job_result)
        self.sio.on("heartbeat", self._on_heartbeat)

    # -- helpers --------------------------------------------------------------------------

    @property
    def rt(self) -> Runtime:
        runtime: Runtime = self.app.state.runtime
        return runtime

    def _client_ip(self, environ: dict[str, Any]) -> str:
        scope = environ.get("asgi.scope") or {}
        client = scope.get("client")
        return str(client[0]) if client else "unknown"

    async def _refuse(self, ip: str, code: str) -> NoReturn:
        if code == "INVALID_KEY":
            await rate_limit.record_failure(self.rt.redis, ip)
        log.info("socket_refused", code=code)
        raise socketio.exceptions.ConnectionRefusedError(code)

    # -- events ---------------------------------------------------------------------------

    async def _on_connect(
        self, sid: str, environ: dict[str, Any], auth: dict[str, Any] | None
    ) -> None:
        ip = self._client_ip(environ)
        settings = self.rt.settings
        if await rate_limit.blocked_for(self.rt.redis, ip, settings.auth_fail_limit_per_minute):
            await self._refuse(ip, "RATE_LIMITED")
        data = auth if isinstance(auth, dict) else {}
        api_key = data.get("apiKey")
        device_id = data.get("deviceId")
        if not isinstance(api_key, str):
            await self._refuse(ip, "INVALID_KEY")
        if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
            await self._refuse(ip, "INVALID_DEVICE")

        async with self.app.state.session_factory() as db:
            try:
                ctx = await authenticate(db, api_key)
            except ApiError as exc:
                await self._refuse(ip, exc.code)
            if ctx.is_test:
                await self._refuse(ip, "TEST_KEY_NOT_ALLOWED")  # test keys use the virtual plugin
            wa_number = digits_only(str(data.get("waNumber") or ""))
            if (
                wa_number != ctx.vendor.phone_e164
                and settings.wa_number_mismatch_policy == "reject"
            ):
                await self._refuse(ip, "NUMBER_MISMATCH")
            version = data.get("version")
            version = version if isinstance(version, str) and _VERSION.fullmatch(version) else None
            vendor_id = ctx.vendor.id

            claim = await presence.claim(self.rt.redis, vendor_id, sid, device_id)
            if not claim.ok:
                await self._refuse(ip, "SESSION_ALREADY_ACTIVE")
            await self._remember_device(db, vendor_id, device_id, wa_number or None, version)

        if claim.replaced_sid and claim.replaced_sid != sid:
            # Same device, new connection: the old socket is a ghost. Drop it.
            await self.sio.disconnect(claim.replaced_sid)
        await self.sio.save_session(sid, {"vendor_id": str(vendor_id), "device_id": device_id})
        await self.sio.enter_room(sid, vendor_room(str(vendor_id)))
        log.info("socket_connected", vendor_id=str(vendor_id))
        await self._wake_dispatch(vendor_id)

    async def _remember_device(
        self,
        db: Any,
        vendor_id: uuid.UUID,
        device_id: str,
        wa_number: str | None,
        version: str | None,
    ) -> None:
        statement = pg_insert(Device).values(
            vendor_id=vendor_id,
            device_id=device_id,
            wa_number=wa_number,
            plugin_version=version,
        )
        await db.execute(
            statement.on_conflict_do_update(
                constraint="uq_devices_vendor_device",
                set_={
                    "wa_number": wa_number,
                    "plugin_version": version,
                    "last_seen_at": func.now(),
                },
            )
        )
        await db.commit()

    async def _wake_dispatch(self, vendor_id: uuid.UUID) -> None:
        try:
            await self.rt.scheduler.dispatch(str(vendor_id))
        except Exception as exc:
            log.warning("socket_wake_failed", error_type=type(exc).__name__)

    async def _vendor_of(self, sid: str) -> tuple[uuid.UUID, str] | None:
        session = await self.sio.get_session(sid)
        if not session or "vendor_id" not in session:
            return None
        return uuid.UUID(session["vendor_id"]), session["device_id"]

    async def _on_disconnect(self, sid: str, *_: Any) -> None:
        who = await self._vendor_of(sid)
        if who is not None:
            await presence.release(self.rt.redis, who[0], sid)
            log.info("socket_disconnected", vendor_id=str(who[0]))

    async def _on_job_ack(self, sid: str, data: Any) -> dict[str, Any]:
        who = await self._vendor_of(sid)
        if who is None:
            return {"ok": False, "error": "NOT_CONNECTED"}
        try:
            payload = AckPayload.model_validate(data)
        except ValidationError:
            return {"ok": False, "error": "INVALID_PAYLOAD"}
        async with self.app.state.session_factory() as db:
            applied = await apply_started(db, who[0], payload.jobId, payload.attempt)
        if applied.outcome is Outcome.NOT_FOUND:
            log.warning("job_ack_foreign", vendor_id=str(who[0]))
            return {"ok": False, "error": "UNKNOWN_JOB"}
        return {"ok": True, "outcome": applied.outcome.value}

    async def _on_job_result(self, sid: str, data: Any) -> dict[str, Any]:
        who = await self._vendor_of(sid)
        if who is None:
            return {"ok": False, "error": "NOT_CONNECTED"}
        try:
            payload = ResultPayload.model_validate(data)
        except ValidationError:
            return {"ok": False, "error": "INVALID_PAYLOAD"}
        async with self.app.state.session_factory() as db:
            vendor = await db.get(Vendor, who[0])
            if vendor is None:
                return {"ok": False, "error": "NOT_CONNECTED"}
            applied = await apply_result(
                db,
                self.rt,
                vendor,
                payload.jobId,
                payload.attempt,
                payload.status,
                wa_ack=payload.waAck,
                error_code=payload.errorCode,
            )
        if applied.outcome is Outcome.NOT_FOUND:
            # A plugin reporting on a job that is not its vendor's: security log, no detail back.
            log.warning("job_result_foreign", vendor_id=str(who[0]))
            return {"ok": False, "error": "UNKNOWN_JOB"}
        return {"ok": True, "outcome": applied.outcome.value}

    async def _on_heartbeat(self, sid: str, data: Any) -> dict[str, Any]:
        who = await self._vendor_of(sid)
        if who is None:
            return {"ok": False, "error": "NOT_CONNECTED"}
        try:
            payload = HeartbeatPayload.model_validate(data or {})
        except ValidationError:
            return {"ok": False, "error": "INVALID_PAYLOAD"}
        vendor_id = who[0]
        redis = self.rt.redis
        before = await presence.wa_state(redis, vendor_id)
        if not await presence.refresh(redis, vendor_id, sid, payload.waState):
            await self.sio.disconnect(sid)  # somebody else took the line, or it expired
            return {"ok": False, "error": "SESSION_LOST"}
        async with self.app.state.session_factory() as db:
            await db.execute(
                update(Device)
                .where(Device.vendor_id == vendor_id, Device.device_id == who[1])
                .values(last_seen_at=jobs.now_utc())
            )
            await db.commit()
        if payload.waState == presence.WA_CONNECTED and before != presence.WA_CONNECTED:
            await self._wake_dispatch(vendor_id)  # WhatsApp is back: send what was waiting
        return {"ok": True, "t": int(time.time())}

    # -- called by the admin panel -------------------------------------------------------

    async def kick_vendor(self, vendor_id: uuid.UUID) -> bool:
        """Drop the plugin's connection right now (key revoked, vendor suspended)."""
        sid = await presence.holder_sid(self.rt.redis, vendor_id)
        if sid is None:
            return False
        await presence.release(self.rt.redis, vendor_id, sid)
        await self.sio.disconnect(sid)
        return True
