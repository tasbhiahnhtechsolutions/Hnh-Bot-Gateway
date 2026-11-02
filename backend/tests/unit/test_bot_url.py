import pytest

from app.core.errors import ApiError
from app.services.bot_url import Resolver, validate_bot_url


def resolver_for(*ips: str) -> Resolver:
    async def resolve(_host: str) -> list[str]:
        return list(ips)

    return resolve


async def check(url: str, *ips: str, allow_private: bool = False) -> list[str]:
    return await validate_bot_url(url, allow_private=allow_private, resolver=resolver_for(*ips))


async def code_of(url: str, *ips: str, allow_private: bool = False) -> str:
    with pytest.raises(ApiError) as info:
        await check(url, *ips, allow_private=allow_private)
    return info.value.code


async def test_public_https_url_passes_and_returns_vetted_ips() -> None:
    assert await check("https://bot.vendor.com/x", "93.184.216.34") == ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost",
        "https://127.0.0.1",
        "https://127.1.2.3:8443",
        "https://10.0.0.5",
        "https://172.16.0.1",
        "https://192.168.1.1",
        "https://169.254.169.254/latest/meta-data",
        "https://100.64.0.1",
        "https://0.0.0.0",
        "https://[::1]",
        "https://[fe80::1]",
        "https://[fc00::1]",
        "https://[::ffff:127.0.0.1]",
        "https://[::ffff:10.0.0.1]",
        "https://224.0.0.1",
    ],
)
async def test_non_public_ip_literals_are_rejected(url: str) -> None:
    assert await code_of(url) == "BOT_URL_NOT_ALLOWED"


@pytest.mark.parametrize(
    "ips",
    [["127.0.0.1"], ["169.254.169.254"], ["10.1.1.1"], ["::1"], ["93.184.216.34", "10.0.0.1"], []],
)
async def test_hostname_resolving_to_non_public_or_nothing_is_rejected(ips: list[str]) -> None:
    assert await code_of("https://innocent.example.com", *ips) == "BOT_URL_NOT_ALLOWED"


async def test_http_is_rejected_outside_dev() -> None:
    assert await code_of("http://bot.vendor.com", "93.184.216.34") == "BOT_URL_MUST_BE_HTTPS"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "bot.vendor.com",
        "ftp://bot.vendor.com",
        "file:///etc/passwd",
        "https://",
        "javascript:alert(1)",
    ],
)
async def test_malformed_or_foreign_schemes_are_rejected_everywhere(url: str) -> None:
    assert await code_of(url, "93.184.216.34") == "INVALID_REQUEST"
    assert await code_of(url, allow_private=True) == "INVALID_REQUEST"


async def test_credentials_in_url_are_rejected() -> None:
    assert await code_of("https://user:pw@bot.vendor.com", "93.184.216.34") == "BOT_URL_NOT_ALLOWED"


async def test_bad_port_is_rejected() -> None:
    assert await code_of("https://bot.vendor.com:99999", "93.184.216.34") == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "url", ["http://localhost:9000", "http://127.0.0.1:9000", "https://10.0.0.5"]
)
async def test_private_urls_allowed_when_flag_is_on(url: str) -> None:
    assert await check(url, allow_private=True) == []
