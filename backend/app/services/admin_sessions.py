"""Admin sessions and login throttling in Redis (plan section 9.3).

Server-side sessions: the cookie holds only a random id, so logout and a deactivated admin
take effect at once. Redis down means no admin login (we fail closed here, unlike the
vendor-key rate limit).
"""

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

LOGIN_WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_ACCOUNT = 5
MAX_FAILURES_PER_IP = 20


@dataclass(frozen=True, slots=True)
class AdminSession:
    admin_id: uuid.UUID
    csrf_token: str


def _session_key(session_id: str) -> str:
    return "adminsess:" + hashlib.sha256(session_id.encode()).hexdigest()


async def create_session(redis: Redis, admin_id: uuid.UUID, ttl_seconds: int) -> tuple[str, str]:
    """Returns ``(session_id, csrf_token)``."""
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    data = json.dumps({"admin_id": str(admin_id), "csrf": csrf_token})
    await redis.set(_session_key(session_id), data, ex=ttl_seconds)
    return session_id, csrf_token


async def load_session(redis: Redis, session_id: str | None) -> AdminSession | None:
    if not session_id or len(session_id) > 100:
        return None
    raw = await redis.get(_session_key(session_id))
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return AdminSession(uuid.UUID(data["admin_id"]), str(data["csrf"]))
    except (ValueError, KeyError, TypeError):
        return None


async def destroy_session(redis: Redis, session_id: str | None) -> None:
    if session_id:
        await redis.delete(_session_key(session_id))


# --- login throttling ------------------------------------------------------------------------


def _account_key(email: str) -> str:
    return "adminfail:acct:" + hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _ip_key(client_ip: str) -> str:
    return f"adminfail:ip:{client_ip}"


async def login_blocked_for(redis: Redis, email: str, client_ip: str) -> int:
    """Seconds to wait before another login attempt, or 0."""
    waits = [0]
    for key, limit in (
        (_account_key(email), MAX_FAILURES_PER_ACCOUNT),
        (_ip_key(client_ip), MAX_FAILURES_PER_IP),
    ):
        count = await redis.get(key)
        if count is not None and int(count) >= limit:
            waits.append(max(int(await redis.ttl(key)), 1))
    return max(waits)


async def record_login_failure(redis: Redis, email: str, client_ip: str) -> None:
    for key in (_account_key(email), _ip_key(client_ip)):
        if await redis.incr(key) == 1:
            await redis.expire(key, LOGIN_WINDOW_SECONDS)


async def clear_login_failures(redis: Redis, email: str) -> None:
    await redis.delete(_account_key(email))


# --- two-factor ------------------------------------------------------------------------------

PENDING_LOGIN_SECONDS = 5 * 60
SETUP_SECONDS = 10 * 60
MAX_CODE_ATTEMPTS = 5


def _pending_key(token: str) -> str:
    return "admin2fa:pending:" + hashlib.sha256(token.encode()).hexdigest()


async def create_pending_login(redis: Redis, admin_id: uuid.UUID) -> str:
    """The password was right but a code is still needed. The token is only good for this step."""
    token = secrets.token_urlsafe(32)
    await redis.set(
        _pending_key(token),
        json.dumps({"admin_id": str(admin_id), "tries": 0}),
        ex=PENDING_LOGIN_SECONDS,
    )
    return token


async def peek_pending_login(redis: Redis, token: str | None) -> uuid.UUID | None:
    if not token or len(token) > 100:
        return None
    raw = await redis.get(_pending_key(token))
    if raw is None:
        return None
    try:
        return uuid.UUID(json.loads(raw)["admin_id"])
    except (ValueError, KeyError, TypeError):
        return None


async def count_code_attempt(redis: Redis, token: str) -> bool:
    """False when the token has used up its tries (it is deleted then)."""
    key = _pending_key(token)
    raw = await redis.get(key)
    if raw is None:
        return False
    data = json.loads(raw)
    data["tries"] += 1
    if data["tries"] >= MAX_CODE_ATTEMPTS:
        await redis.delete(key)
        return False
    ttl = await redis.ttl(key)
    await redis.set(key, json.dumps(data), ex=max(ttl, 1))
    return True


async def finish_pending_login(redis: Redis, token: str) -> None:
    await redis.delete(_pending_key(token))


async def claim_code_step(redis: Redis, admin_id: uuid.UUID, step: int) -> bool:
    """A code works once: False when this admin already used this 30-second step."""
    return bool(await redis.set(f"admin2fa:used:{admin_id}:{step}", "1", ex=120, nx=True))


async def save_setup_secret(redis: Redis, admin_id: uuid.UUID, secret: str) -> None:
    await redis.set(f"admin2fa:setup:{admin_id}", secret, ex=SETUP_SECONDS)


async def load_setup_secret(redis: Redis, admin_id: uuid.UUID) -> str | None:
    raw = await redis.get(f"admin2fa:setup:{admin_id}")
    return raw.decode() if raw else None


async def clear_setup_secret(redis: Redis, admin_id: uuid.UUID) -> None:
    await redis.delete(f"admin2fa:setup:{admin_id}")
