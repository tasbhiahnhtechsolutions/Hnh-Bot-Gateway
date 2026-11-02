"""Bot and webhook URL validation, the SSRF guard (plan section 6.1)."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from app.core.errors import ApiError

Resolver = Callable[[str], Awaitable[list[str]]]


async def resolve_host(hostname: str) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({str(info[4][0]) for info in infos})


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 is still loopback
    return ip.is_global and not ip.is_multicast


async def validate_bot_url(
    url: str, *, allow_private: bool, resolver: Resolver = resolve_host
) -> list[str]:
    """Raise ``ApiError`` if the URL must not be called. Returns the vetted public IPs.

    In dev/test (``allow_private``) any http(s) URL passes and the list is empty. Otherwise the
    URL must be https and every address the host resolves to must be public. The caller connects
    to one of the returned IPs, so a DNS answer that changes later (DNS rebinding) cannot
    redirect the request to an internal address.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        parsed.port  # noqa: B018  (raises ValueError on a bad port)
    except ValueError as exc:
        raise ApiError(400, "INVALID_REQUEST", "URL is not valid") from exc

    if parsed.scheme not in ("http", "https") or not hostname:
        raise ApiError(400, "INVALID_REQUEST", "URL must start with http:// or https://")
    if parsed.username is not None or parsed.password is not None:
        raise ApiError(400, "BOT_URL_NOT_ALLOWED", "URL must not contain credentials")

    if allow_private:
        return []
    if parsed.scheme != "https":
        raise ApiError(400, "BOT_URL_MUST_BE_HTTPS", "bot URL must use https")

    try:
        ipaddress.ip_address(hostname)
        addresses = [hostname]
    except ValueError:
        addresses = await resolver(hostname)

    if not addresses or not all(_is_public(a) for a in addresses):
        raise ApiError(400, "BOT_URL_NOT_ALLOWED", "bot URL does not point to a public address")
    return addresses
