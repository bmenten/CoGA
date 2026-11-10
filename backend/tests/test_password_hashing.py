"""Password hashing: bcrypt, called directly, at cost 12, on a password's first 72 bytes."""

from backend.app.dependencies import get_password_hash, verify_password


def test_a_hash_is_bcrypt_at_cost_12_and_verifies_its_password_only() -> None:
    made = get_password_hash("a new passphrase")
    assert made.startswith("$2b$12$")
    assert verify_password("a new passphrase", made)
    assert not verify_password("another passphrase", made)


def test_a_long_new_password_is_hashed_on_its_first_72_bytes() -> None:
    made = get_password_hash("é" * 50)  # 100 bytes in UTF-8
    assert verify_password("é" * 50, made)
    assert verify_password("é" * 36, made)  # the same first 72 bytes
    assert not verify_password("é" * 35, made)


def test_a_value_that_is_not_a_bcrypt_hash_never_matches() -> None:
    for stored in ("", "not-a-hash", "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ", None):
        assert verify_password("anything", stored) is False  # type: ignore[arg-type]
