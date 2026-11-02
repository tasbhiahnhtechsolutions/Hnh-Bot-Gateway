"""Admin panel hardening: security headers and the optional IP allowlist (plan section 9.3)."""

import ipaddress
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from app.core.config import ConfigError

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# No inline script or style anywhere, and no external hosts: htmx etc. would be served from
# our own static files, never a CDN.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'self'; img-src 'self'; script-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


def parse_allowlist(raw: str) -> list[IPNetwork]:
    networks: list[IPNetwork] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError as exc:
            raise ConfigError(f"ADMIN_IP_ALLOWLIST has an invalid entry: {item!r}") from exc
    return networks


def ip_allowed(client_ip: str | None, networks: list[IPNetwork]) -> bool:
    if not networks:
        return True
    try:
        address = ipaddress.ip_address(client_ip or "")
    except ValueError:
        return False
    return any(address in network for network in networks)


def install_admin_security(app: FastAPI, allowlist: list[IPNetwork]) -> None:
    @app.middleware("http")
    async def admin_security(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not request.url.path.startswith("/admin"):
            return await call_next(request)
        client_ip = request.client.host if request.client else None
        if not ip_allowed(client_ip, allowlist):
            return PlainTextResponse("Forbidden", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
