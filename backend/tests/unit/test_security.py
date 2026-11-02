import pytest
from cryptography.fernet import Fernet

from app.core.security import (
    KeyKind,
    SecretBox,
    generate_api_key,
    generate_signing_secret,
    hash_api_key,
    parse_api_key,
    verify_api_key,
)


@pytest.mark.parametrize("kind", list(KeyKind))
def test_generated_key_has_expected_shape(kind: KeyKind) -> None:
    generated = generate_api_key(kind)
    assert generated.full_key.startswith(f"wab_{kind.value}_{generated.prefix}_")
    assert len(generated.prefix) == 8
    assert generated.key_hash == hash_api_key(generated.full_key)
    assert generated.key_hash != generated.full_key


def test_verify_accepts_right_key_and_rejects_wrong_one() -> None:
    generated = generate_api_key()
    assert verify_api_key(generated.full_key, generated.key_hash)
    assert not verify_api_key(generated.full_key + "x", generated.key_hash)
    assert not verify_api_key("", generated.key_hash)


def test_keys_are_unique() -> None:
    keys = {generate_api_key().full_key for _ in range(200)}
    assert len(keys) == 200


def test_parse_roundtrip_even_when_secret_contains_underscores() -> None:
    for _ in range(50):
        generated = generate_api_key(KeyKind.TEST)
        parsed = parse_api_key(generated.full_key)
        assert parsed is not None
        assert parsed.kind is KeyKind.TEST
        assert parsed.prefix == generated.prefix


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "wab",
        "wab_live_abcd1234",
        "wab_live_abcd1234_",
        "wab_live__secret",
        "xyz_live_abcd1234_secret",
        "wab_prod_abcd1234_secret",
        "Bearer wab_live_abcd1234_secret",
    ],
)
def test_parse_rejects_malformed_keys(bad: str) -> None:
    assert parse_api_key(bad) is None


def test_signing_secret_format() -> None:
    first, second = generate_signing_secret(), generate_signing_secret()
    assert first.startswith("whsec_")
    assert first != second


def test_secret_box_roundtrip_and_wrong_key() -> None:
    box = SecretBox(Fernet.generate_key())
    token = box.encrypt("whsec_hello")
    assert token != "whsec_hello"
    assert box.decrypt(token) == "whsec_hello"
    with pytest.raises(ValueError, match="cannot decrypt"):
        SecretBox(Fernet.generate_key()).decrypt(token)
