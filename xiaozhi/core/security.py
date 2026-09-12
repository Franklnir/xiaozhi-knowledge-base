import hashlib
import hmac
import re
import secrets
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import jwt
from cryptography.fernet import InvalidToken

from xiaozhi.config import MCP_TOKEN_HASH_LENGTH, fernet

# JWT Configuration
JWT_SECRET = secrets.token_urlsafe(64)  # In production, use env var
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE = 3600  # 1 hour
JWT_REFRESH_TOKEN_EXPIRE = 604800  # 7 days


def normalize_username(username: str) -> str:
    username = (username or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{3,32}", username):
        raise ValueError("Username hanya boleh huruf kecil, angka, dan underscore (3-32 karakter).")
    return username


def normalize_username_prefix(prefix: str) -> str:
    prefix = (prefix or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{3,32}", prefix):
        raise ValueError("Pencarian akun minimal 3 karakter dan hanya boleh huruf, angka, underscore.")
    return prefix


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password minimal 8 karakter.")
    if len(password) > 128:
        raise ValueError("Password maksimal 128 karakter.")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        310_000,
    ).hex()
    return f"pbkdf2_sha256$310000${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            int(iterations),
        ).hex()
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def encrypt_secret(value: str) -> str:
    return fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> Optional[str]:
    try:
        return fernet.decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def mask_secret(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        tail = value[-6:] if len(value) > 6 else "***"
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?token=...{tail}"
    if len(value) <= 10:
        return "********"
    return f"{value[:4]}...{value[-4:]}"


def xiaozhi_token_hash(token: str) -> str:
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:MCP_TOKEN_HASH_LENGTH]


def normalize_token_hash(value: str) -> str:
    return re.sub(r"[^a-f0-9]", "", (value or "").lower())[:MCP_TOKEN_HASH_LENGTH]


def history_matches_token(item: Dict[str, Any], token_hash: str) -> bool:
    current_hash = normalize_token_hash(token_hash)
    if not current_hash:
        return True
    item_hash = normalize_token_hash(str(item.get("token_hash", "")))
    return not item_hash or item_hash == current_hash


# ── JWT Functions ──────────────────────────────────────────────────────────

def create_access_token(user_id: int, username: str, role: str, session_version: int = 1) -> str:
    """Create a JWT access token for API authentication."""
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "sv": session_version,
        "type": "access",
        "exp": int(time.time()) + JWT_ACCESS_TOKEN_EXPIRE,
        "iat": int(time.time()),
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: int, username: str, session_version: int = 1) -> str:
    """Create a JWT refresh token for obtaining new access tokens."""
    payload = {
        "sub": str(user_id),
        "username": username,
        "sv": session_version,
        "type": "refresh",
        "exp": int(time.time()) + JWT_REFRESH_TOKEN_EXPIRE,
        "iat": int(time.time()),
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_token_pair(user_id: int, username: str, role: str, session_version: int = 1) -> Dict[str, Any]:
    """Create both access and refresh tokens."""
    return {
        "access_token": create_access_token(user_id, username, role, session_version),
        "refresh_token": create_refresh_token(user_id, username, session_version),
        "token_type": "bearer",
        "expires_in": JWT_ACCESS_TOKEN_EXPIRE,
    }


def decode_jwt_token(token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Decode and validate a JWT token.
    Returns (payload, error_message). If valid, error_message is None.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload, None
    except jwt.ExpiredSignatureError:
        return None, "Token sudah kedaluwarsa."
    except jwt.InvalidTokenError as exc:
        return None, f"Token tidak valid: {str(exc)}"


def validate_access_token(token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate an access token and return user info."""
    payload, error = decode_jwt_token(token)
    if error:
        return None, error
    if payload.get("type") != "access":
        return None, "Token bukan access token."
    return {
        "user_id": int(payload["sub"]),
        "username": payload.get("username", ""),
        "role": payload.get("role", "user"),
        "session_version": payload.get("sv", 1),
    }, None


def validate_refresh_token(token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate a refresh token."""
    payload, error = decode_jwt_token(token)
    if error:
        return None, error
    if payload.get("type") != "refresh":
        return None, "Token bukan refresh token."
    return {
        "user_id": int(payload["sub"]),
        "username": payload.get("username", ""),
        "session_version": payload.get("sv", 1),
    }, None


def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    """Extract token from Authorization header."""
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None
