"""
API v1 Authentication endpoints for mobile and API clients.
Uses JWT tokens instead of session cookies.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from xiaozhi.core.security import (
    create_token_pair,
    validate_access_token,
    validate_refresh_token,
    verify_password,
)
from xiaozhi.dependencies import get_store

router = APIRouter(prefix="/api/v1/auth", tags=["API v1 Auth"])


# ── Request/Response Models ────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, pattern=r"[a-z0-9_]+")
    password: str = Field(..., min_length=6, max_length=128)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, pattern=r"[a-z0-9_]+")
    password: str = Field(..., min_length=6, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    success: bool = True
    data: dict = None
    message: str = "OK"


class ApiError(BaseModel):
    success: bool = False
    data: None = None
    message: str


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse)
async def api_register(body: RegisterRequest):
    """
    Register a new user account and return JWT tokens.
    """
    store = get_store()
    try:
        user = store.create_user(body.username, body.password)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"success": False, "data": None, "message": str(exc)}
        )

    user_id = int(user["id"])
    role = str(user.get("role") or "user").lower()
    session_version = max(1, int(user.get("session_version", 1) or 1))

    tokens = create_token_pair(user_id, body.username, role, session_version)

    return TokenResponse(
        success=True,
        data={
            "user": {
                "id": user_id,
                "username": body.username,
                "role": role,
            },
            **tokens,
        },
        message="Registrasi berhasil."
    )


@router.post("/login", response_model=TokenResponse)
async def api_login(body: LoginRequest):
    """
    Login with username and password.
    Returns JWT access and refresh tokens for mobile/API clients.
    """
    store = get_store()
    user_record = store.get_user_by_username(body.username)

    if not user_record or not verify_password(body.password, user_record.get("password_hash", "")):
        raise HTTPException(
            status_code=401,
            detail={"success": False, "data": None, "message": "Username atau password salah."}
        )

    role = str(user_record.get("role") or "user").lower()
    user_id = int(user_record["id"])
    session_version = max(1, int(user_record.get("session_version", 1) or 1))

    tokens = create_token_pair(user_id, body.username, role, session_version)

    return TokenResponse(
        success=True,
        data={
            "user": {
                "id": user_id,
                "username": body.username,
                "role": role,
            },
            **tokens,
        },
        message="Login berhasil."
    )


@router.post("/refresh", response_model=TokenResponse)
async def api_refresh(body: RefreshRequest):
    """
    Refresh access token using refresh token.
    Returns new access token.
    """
    store = get_store()
    user_info, error = validate_refresh_token(body.refresh_token)

    if error or not user_info:
        raise HTTPException(
            status_code=401,
            detail={"success": False, "data": None, "message": error or "Refresh token tidak valid."}
        )

    user = store.get_user(user_info["user_id"])
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"success": False, "data": None, "message": "User tidak ditemukan."}
        )

    # Verify session version matches
    current_sv = max(1, int(user.get("session_version", 1) or 1))
    if current_sv != user_info.get("session_version", 1):
        raise HTTPException(
            status_code=401,
            detail={"success": False, "data": None, "message": "Session telah kedaluwarsa. Silakan login ulang."}
        )

    role = str(user.get("role") or "user").lower()
    tokens = create_token_pair(user["id"], user["username"], role, current_sv)

    return TokenResponse(
        success=True,
        data={
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": role,
            },
            **tokens,
        },
        message="Token berhasil diperbarui."
    )


@router.get("/me", response_model=TokenResponse)
async def api_me(request: Request):
    """
    Get current authenticated user info.
    Requires Authorization: Bearer <token>
    """
    from xiaozhi.dependencies import get_current_user

    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"success": False, "data": None, "message": "Tidak terotentikasi."}
        )

    return TokenResponse(
        success=True,
        data={
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": user.get("role", "user"),
                "ui_theme": user.get("ui_theme", "neo"),
            }
        },
        message="OK"
    )
