"""
API v1 Authentication endpoints for mobile and API clients.
Uses JWT tokens instead of session cookies.
"""
import requests
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
                "google_id": user.get("google_id"),
                "google_email": user.get("google_email"),
                "registered_with_google": user.get("registered_with_google", False),
            }
        },
        message="OK"
    )


@router.post("/google", response_model=TokenResponse)
async def api_google_auth(body: GoogleAuthRequest, request: Request):
    """
    Authenticate, register, or link with Google ID token (for Android & Mobile clients).
    """
    token_str = body.id_token.strip()
    if not token_str:
        raise HTTPException(
            status_code=400,
            detail={"success": False, "data": None, "message": "ID token Google diperlukan."}
        )

    # Verify token with Google
    google_id = None
    google_email = None
    google_name = None

    try:
        resp = requests.get(f"https://oauth2.googleapis.com/tokeninfo?id_token={token_str}", timeout=10)
        if resp.ok:
            payload = resp.json()
            google_id = payload.get("sub")
            google_email = (payload.get("email") or "").strip().lower()
            google_name = payload.get("name") or ""
        else:
            resp2 = requests.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {token_str}"},
                timeout=10,
            )
            if resp2.ok:
                payload2 = resp2.json()
                google_id = str(payload2.get("id") or "")
                google_email = (payload2.get("email") or "").strip().lower()
                google_name = payload2.get("name") or ""
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"success": False, "data": None, "message": f"Gagal memverifikasi token Google: {exc}"}
        )

    if not google_id or not google_email:
        raise HTTPException(
            status_code=400,
            detail={"success": False, "data": None, "message": "Token Google tidak valid atau tidak memiliki email."}
        )

    store = get_store()
    action = body.action.strip().lower()

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
            return TokenResponse(
                success=True,
                data={
                    "user": {
                        "id": current_user["id"],
                        "username": current_user["username"],
                        "role": current_user.get("role", "user"),
                        "google_id": google_id,
                        "google_email": google_email,
                        "registered_with_google": current_user.get("registered_with_google", False),
                    }
                },
                message=f"Akun Google ({google_email}) berhasil ditautkan!"
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"success": False, "data": None, "message": str(exc)}
            )

    # ── Action: REGISTER ──
    if action == "register":
        existing_user = store.get_user_by_google_id(google_id)
        if not existing_user and google_email:
            existing_user = store.get_user_by_email(google_email)

        if existing_user:
            raise HTTPException(
                status_code=400,
                detail={"success": False, "data": None, "message": f"Akun Google ({google_email}) sudah terdaftar. Silakan langsung masuk."}
            )

        from xiaozhi.routers.google_auth import generate_unique_username
        username = generate_unique_username(google_email, google_name, store)
        try:
            new_user = store.create_google_user(username, google_id, google_email)
            user_id = int(new_user["id"])
            role = str(new_user.get("role") or "user").lower()
            session_version = 1
            tokens = create_token_pair(user_id, username, role, session_version)
            return TokenResponse(
                success=True,
                data={
                    "user": {
                        "id": user_id,
                        "username": username,
                        "role": role,
                        "google_id": google_id,
                        "google_email": google_email,
                        "registered_with_google": True,
                    },
                    **tokens,
                },
                message="Registrasi dengan Google berhasil."
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"success": False, "data": None, "message": str(exc)}
            )

    # ── Action: LOGIN ──
    user_record = store.get_user_by_google_id(google_id)
    if not user_record and google_email:
        user_record = store.get_user_by_email(google_email)
        if user_record:
            try:
                store.link_google_account(int(user_record["id"]), google_id, google_email)
            except Exception:
                pass

    if not user_record:
        raise HTTPException(
            status_code=404,
            detail={"success": False, "data": None, "message": f"Akun Google ({google_email}) belum terdaftar. Silakan daftar terlebih dahulu melalui tab Daftar."}
        )

    user_id = int(user_record["id"])
    role = str(user_record.get("role") or "user").lower()
    session_version = max(1, int(user_record.get("session_version", 1) or 1))
    tokens = create_token_pair(user_id, user_record["username"], role, session_version)

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
            },
            **tokens,
        },
        message="Login dengan Google berhasil."
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
