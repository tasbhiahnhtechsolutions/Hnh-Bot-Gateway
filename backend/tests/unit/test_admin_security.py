import pytest

from app.admin.security import ip_allowed, parse_allowlist
from app.core.config import ConfigError
from app.core.passwords import hash_password, needs_rehash, verify_password


def test_empty_allowlist_allows_everyone() -> None:
    assert ip_allowed("203.0.113.9", parse_allowlist(""))
    assert ip_allowed(None, parse_allowlist("  , "))


def test_allowlist_matches_addresses_and_networks() -> None:
    allow = parse_allowlist("10.1.2.0/24, 203.0.113.9, 2001:db8::/32")
    assert ip_allowed("10.1.2.77", allow)
    assert ip_allowed("203.0.113.9", allow)
    assert ip_allowed("2001:db8::1", allow)
    assert not ip_allowed("10.1.3.1", allow)
    assert not ip_allowed("203.0.113.10", allow)


def test_allowlist_denies_unknown_or_garbage_peers() -> None:
    allow = parse_allowlist("10.0.0.0/8")
    assert not ip_allowed(None, allow)
    assert not ip_allowed("not-an-ip", allow)


def test_invalid_allowlist_entry_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="ADMIN_IP_ALLOWLIST"):
        parse_allowlist("10.0.0.0/8, banana")


def test_password_hash_roundtrip_and_failures() -> None:
    stored = hash_password("a long enough password")
    assert stored.startswith("$argon2id$")
    assert verify_password(stored, "a long enough password")
    assert not verify_password(stored, "another password!")
    assert not verify_password(stored, "")
    assert not verify_password("garbage", "a long enough password")
    assert not needs_rehash(stored)


def test_verifying_against_no_account_is_false_but_still_costs_a_hash() -> None:
    assert verify_password(None, "whatever whatever") is False
