"""
API v1 Authentication endpoints for mobile and API clients.
Uses JWT tokens instead of session cookies.
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, Field

from xiaozhi.config import GOOGLE_AUTH_ENABLED, GOOGLE_CLIENT_ID
from xiaozhi.core.security import (
    create_token_pair,
    validate_access_token,
    validate_refresh_token,
    verify_password,
)
from xiaozhi.core.rate_limiter import enforce_predefined_limit
from xiaozhi.dependencies import get_store
from xiaozhi.services.mcp_service import is_mcp_connected

router = APIRouter(prefix="/api/v1/auth", tags=["API v1 Auth"])
logger = logging.getLogger("xiaozhi.api_v1_auth")


# ── Request/Response Models ────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, pattern=r"[a-z0-9_]+")
    password: str = Field(..., min_length=6, max_length=128)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, pattern=r"[a-z0-9_]+")
    password: str = Field(..., min_length=6, max_length=128)


class GoogleAuthRequest(BaseModel):
    id_token: str
    action: str = "login"  # "login", "register", "link"


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
async def api_register(body: RegisterRequest, request: Request):
    """
    Register a new user account and return JWT tokens.
    """
    enforce_predefined_limit(request, "register")
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
    mcp_required = (role != "admin")
    mcp_connected = bool(is_mcp_connected(user_id))

    return TokenResponse(
        success=True,
        data={
            "user": {
                "id": user_id,
                "username": body.username,
                "role": role,
                "firebase_uid": user.get("firebase_uid"),
                "firebase_email": user.get("firebase_email"),
            },
            **tokens,
            "mcp_required": mcp_required,
            "mcp_connected": mcp_connected,
        },
        message="Registrasi berhasil."
    )


@router.post("/login", response_model=TokenResponse)
async def api_login(body: LoginRequest, request: Request):
    """
    Login with username and password.
    Returns JWT access and refresh tokens for mobile/API clients.
    """
    enforce_predefined_limit(request, "login")
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
    mcp_required = (role != "admin")
    mcp_connected = bool(is_mcp_connected(user_id))

    return TokenResponse(
        success=True,
        data={
            "user": {
                "id": user_id,
                "username": body.username,
                "role": role,
                "firebase_uid": user_record.get("firebase_uid"),
                "firebase_email": user_record.get("firebase_email"),
            },
            **tokens,
            "mcp_required": mcp_required,
            "mcp_connected": mcp_connected,
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
    mcp_required = (role != "admin")
    mcp_connected = bool(is_mcp_connected(int(user["id"])))

    return TokenResponse(
        success=True,
        data={
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": role,
            },
            **tokens,
            "mcp_required": mcp_required,
            "mcp_connected": mcp_connected,
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

    role = str(user.get("role") or "user").lower()
    mcp_required = (role != "admin")
    mcp_connected = bool(is_mcp_connected(int(user["id"])))

    return TokenResponse(
        success=True,
        data={
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": role,
                "ui_theme": user.get("ui_theme", "neo"),
                "google_id": user.get("google_id"),
                "google_email": user.get("google_email"),
                "registered_with_google": user.get("registered_with_google", False),
            },
            "mcp_required": mcp_required,
            "mcp_connected": mcp_connected,
        },
        message="OK"
    )


@router.post("/google", response_model=TokenResponse)
async def api_google_auth(body: GoogleAuthRequest, request: Request):
    """
    Authenticate, register, or link with Google ID token (for Android & Mobile clients).
    """
    enforce_predefined_limit(request, "login")
    if not GOOGLE_AUTH_ENABLED or not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=503,
            detail={"success": False, "data": None, "message": "Integrasi Google sedang dinonaktifkan."},
        )
    token_str = body.id_token.strip()
    if not token_str:
        raise HTTPException(
            status_code=400,
            detail={"success": False, "data": None, "message": "ID token Google diperlukan."}
        )

    # Accept only an OpenID Connect ID token and bind it to this OAuth client.
    google_id = None
    google_email = None
    google_name = None

    try:
        payload = google_id_token.verify_oauth2_token(
            token_str,
            google_auth_requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=30,
        )
        email_verified = payload.get("email_verified") in (True, "true", "True", "1")
        if not email_verified:
            raise ValueError("Email Google belum terverifikasi.")
        google_id = payload.get("sub")
        google_email = (payload.get("email") or "").strip().lower()
        google_name = payload.get("name") or ""
    except Exception as exc:
        logger.warning("Google API ID token verification failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=401,
            detail={"success": False, "data": None, "message": "Token Google tidak valid atau kedaluwarsa."},
        ) from exc

    if not google_id or not google_email:
        raise HTTPException(
            status_code=400,
            detail={"success": False, "data": None, "message": "Token Google tidak valid atau tidak memiliki email."}
        )

    store = get_store()
    action = body.action.strip().lower()
    if action not in {"login", "register", "link"}:
        raise HTTPException(
            status_code=400,
            detail={"success": False, "data": None, "message": "Aksi autentikasi Google tidak valid."},
        )

    # ── Action: LINK ──
    if action == "link":
        from xiaozhi.dependencies import get_current_user
        current_user = get_current_user(request)
        if not current_user:
            raise HTTPException(
                status_code=401,
                detail={"success": False, "data": None, "message": "Sesi login tidak valid untuk menautkan Google."}
            )
        try:
            store.link_google_account(current_user["id"], google_id, google_email)
            role = str(current_user.get("role") or "user").lower()
            return TokenResponse(
                success=True,
                data={
                    "user": {
                        "id": current_user["id"],
                        "username": current_user["username"],
                        "role": role,
                        "google_id": google_id,
                        "google_email": google_email,
                        "registered_with_google": current_user.get("registered_with_google", False),
                    },
                    "mcp_required": (role != "admin"),
                    "mcp_connected": bool(is_mcp_connected(current_user["id"])),
                },
                message=f"Akun Google ({google_email}) berhasil ditautkan!"
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"success": False, "data": None, "message": str(exc)}
            )

    # ── Seamless LOGIN / REGISTER ──
    # Login and registration are intentionally distinct. Never auto-link an
    # existing local account merely because its email matches a Google claim.
    user_record = store.get_user_by_google_id(google_id)
    if not user_record:
        if store.get_user_by_email(google_email):
            raise HTTPException(
                status_code=409,
                detail={
                    "success": False,
                    "data": None,
                    "message": "Email sudah dipakai. Login dengan password lalu tautkan Google.",
                },
            )
        if action == "login":
            raise HTTPException(
                status_code=404,
                detail={
                    "success": False,
                    "data": None,
                    "message": "Akun Google belum terdaftar. Daftar dengan Google terlebih dahulu.",
                },
            )
        from xiaozhi.routers.google_auth import generate_unique_username
        username = generate_unique_username(google_email, google_name, store)
        try:
            user_record = store.create_google_user(username, google_id, google_email)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"success": False, "data": None, "message": str(exc)}
            )

    user_id = int(user_record["id"])
    role = str(user_record.get("role") or "user").lower()
    session_version = max(1, int(user_record.get("session_version", 1) or 1))

    tokens = create_token_pair(user_id, user_record["username"], role, session_version)
    mcp_required = (role != "admin")
    mcp_connected = bool(is_mcp_connected(user_id))

    return TokenResponse(
        success=True,
        data={
            "user": {
                "id": user_id,
                "username": user_record["username"],
                "role": role,
                "google_id": google_id,
                "google_email": google_email,
                "registered_with_google": bool(user_record.get("registered_with_google", False)),
                "firebase_uid": user_record.get("firebase_uid"),
                "firebase_email": user_record.get("firebase_email"),
            },
            **tokens,
            "mcp_required": mcp_required,
            "mcp_connected": mcp_connected,
        },
        message="Autentikasi Google berhasil."
    )


@router.post("/google/unlink", response_model=TokenResponse)
async def api_google_unlink(request: Request):
    """
    Unlink Google account from current user.
    Cannot be unlinked if user was registered via Google.
    """
    from xiaozhi.dependencies import get_current_user

    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"success": False, "data": None, "message": "Tidak terotentikasi."}
        )

    store = get_store()
    try:
        store.unlink_google_account(user["id"])
        return TokenResponse(
            success=True,
            data={"user": {"id": user["id"], "username": user["username"], "google_id": None, "google_email": None, "registered_with_google": False}},
            message="Tautan akun Google berhasil diputuskan."
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"success": False, "data": None, "message": str(exc)}
        )
