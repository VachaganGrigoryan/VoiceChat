from app.core.security import (
    create_access_token,
    decode_token,
    generate_refresh_token,
    hash_refresh_token,
    hash_verification_code,
    verify_verification_code,
)


def test_access_token_roundtrip():
    token = create_access_token(subject="user123")
    payload = decode_token(token)
    assert payload["sub"] == "user123"
    assert payload["type"] == "access"


def test_refresh_token_hashing():
    token = generate_refresh_token()
    hashed1 = hash_refresh_token(token)
    hashed2 = hash_refresh_token(token)
    assert hashed1 == hashed2
    assert hashed1 != token


def test_verification_code_hashing():
    email = "user@test.com"
    code = "123456"
    hashed = hash_verification_code(email, code)
    assert verify_verification_code(email, code, hashed) is True
    assert verify_verification_code(email, "000000", hashed) is False