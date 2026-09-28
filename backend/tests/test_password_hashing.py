"""Password hashing without passlib (#525).

passlib 1.7.4 is unmaintained and held bcrypt at 3.2.0. The backend now calls bcrypt
directly; these tests pin that every hash passlib stored keeps verifying.
"""

from backend.app.dependencies import get_password_hash, verify_password

# Made by the code this replaces, passlib 1.7.4 with bcrypt 3.2.0:
#   CryptContext(schemes=["bcrypt"]).hash(...)
# of "coga-passlib-compat" and of "L" * 80.
PASSLIB_BCRYPT = "$2b$12$pcsvlI13kXFibfeN43efzuBGhIDhOoLLnuB7YRkioPn840wTRrseu"
PASSLIB_BCRYPT_80_BYTES = "$2b$12$xo6i12hhWYTlDL.PItXpwubip4xC27HGn2YNwp7sMldQksn70uwWG"


def test_a_hash_stored_by_passlib_still_verifies() -> None:
    assert verify_password("coga-passlib-compat", PASSLIB_BCRYPT)
    assert not verify_password("coga-passlib-compat!", PASSLIB_BCRYPT)


def test_a_long_password_stored_by_passlib_still_verifies() -> None:
    # bcrypt reads 72 bytes. passlib let it drop the rest silently; bcrypt 5 would refuse
    # the long password outright, locking the account out.
    assert verify_password("L" * 80, PASSLIB_BCRYPT_80_BYTES)
    assert verify_password("L" * 72, PASSLIB_BCRYPT_80_BYTES)
    assert not verify_password("L" * 71, PASSLIB_BCRYPT_80_BYTES)


def test_new_hashes_keep_passlibs_format_and_cost() -> None:
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
