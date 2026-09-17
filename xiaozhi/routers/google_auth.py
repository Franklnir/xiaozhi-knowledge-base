"""
Google OAuth 2.0 Router for Xiaozhi Indonesia.
Supports:
1. Login & Register with Google.
2. Link Google Account from user profile.
3. Unlink Google Account with confirmation and CSRF verification.
"""
import logging
import os
import re
import secrets
from typing import Optional
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired

from xiaozhi.config import (
    DEFAULT_UI_THEME,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    google_oauth_serializer,
)
from xiaozhi.core.security import normalize_username
from xiaozhi.dependencies import (
    get_current_user,
    get_store,
    redirect_with_message,
    render,
    validate_csrf,
)
from xiaozhi.routers.auth import set_session_cookie
from xiaozhi.services.mcp_service import is_mcp_connected

logger = logging.getLogger("xiaozhi.google_auth")

router = APIRouter(prefix="/api/auth/google", tags=["Google Auth"])


def get_google_redirect_uri(request: Request) -> str:
    """Determine the valid Google OAuth redirect URI matching Google Cloud Console configuration."""
    # Detect host and protocol from request
    forwarded_host = request.headers.get("x-forwarded-host")
    host = forwarded_host or request.headers.get("host") or ""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"

    # Match against authorized redirect URIs in Google Cloud Console:
    # - https://xiaozhiscig.biz.id/api/auth/google/callback
    # - http://localhost:8000/api/auth/google/callback
    # - http://127.0.0.1:8000/api/auth/google/callback
    if "127.0.0.1" in host:
        return "http://127.0.0.1:8000/api/auth/google/callback"
    if "localhost" in host:
        return "http://localhost:8000/api/auth/google/callback"
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    if "xiaozhiscig.biz.id" in host or os.getenv("ENVIRONMENT") == "production":
        return "https://xiaozhiscig.biz.id/api/auth/google/callback"

    return f"{proto}://{host}/api/auth/google/callback"


def generate_unique_username(email: str, name: str, store) -> str:
    """Generate a clean, unique username from email prefix or name."""
    candidate = email.split("@")[0].lower() if email else (name.lower() if name else "user")
    candidate = re.sub(r"[^a-z0-9_]", "_", candidate).strip("_")
    if len(candidate) < 3:
        candidate = f"user_{candidate}"[:32]
    elif len(candidate) > 32:
        candidate = candidate[:32]

    # Verify uniqueness
    base = candidate[:28]
    counter = 1
    final_username = candidate
    while store.get_user_by_username(final_username):
        final_username = f"{base}_{counter}"
        counter += 1

    return final_username


@router.get("/login")
async def google_login(request: Request, intent: str = Query("login")):
    """Initiate Google OAuth flow for Login or Register."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        logger.error("Google OAuth credentials not configured.")
        return redirect_with_message("/login", "Integrasi Google belum dikonfigurasi di server.")

    valid_intent = "register" if intent == "register" else "login"

    # State payload signed with secret key and salt
    state = google_oauth_serializer.dumps({
        "action": valid_intent,
        "nonce": secrets.token_urlsafe(16),
    })

    redirect_uri = get_google_redirect_uri(request)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    google_auth_url = f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"
    return RedirectResponse(url=google_auth_url, status_code=303)


@router.get("/link")
async def google_link(request: Request):
    """Initiate Google OAuth flow to link Google account to current logged-in user."""
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu untuk menautkan Google.")

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return redirect_with_message("/profil", "Integrasi Google belum dikonfigurasi di server.")

    state = google_oauth_serializer.dumps({
        "action": "link",
        "user_id": int(user["id"]),
        "nonce": secrets.token_urlsafe(16),
    })

    redirect_uri = get_google_redirect_uri(request)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    google_auth_url = f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"
    return RedirectResponse(url=google_auth_url, status_code=303)


@router.get("/callback")
async def google_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Handle OAuth 2.0 callback from Google."""
    if error:
        logger.warning("Google OAuth error callback: %s", error)
        return redirect_with_message("/login", f"Autentikasi Google dibatalkan: {error}")

    if not code or not state:
        return redirect_with_message("/login", "Permintaan autentikasi Google tidak lengkap.")

    # Validate state parameter
    try:
        state_data = google_oauth_serializer.loads(state, max_age=600)
    except (SignatureExpired, BadSignature) as exc:
        logger.warning("Invalid or expired Google OAuth state: %s", exc)
        return redirect_with_message("/login", "Sesi autentikasi Google kedaluwarsa. Silakan coba lagi.")

    action = state_data.get("action", "login")

    # Exchange authorization code for tokens
    redirect_uri = get_google_redirect_uri(request)
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        token_resp = requests.post(token_url, data=payload, timeout=10)
        token_data = token_resp.json()
    except Exception as exc:
        logger.error("Failed to exchange code with Google: %s", exc)
        target = "/profil" if action == "link" else ("/register" if action == "register" else "/login")
        return redirect_with_message(target, "Gagal menghubungi server Google. Coba lagi.")

    if not token_resp.ok or "access_token" not in token_data:
        err_msg = token_data.get("error_description") or token_data.get("error") or "Gagal menukar token Google."
        logger.error("Google token exchange error: %s", token_data)
        target = "/profil" if action == "link" else ("/register" if action == "register" else "/login")
        return redirect_with_message(target, f"Error Google: {err_msg}")

    access_token = token_data["access_token"]

    # Fetch Google user profile
    try:
        userinfo_resp = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        userinfo = userinfo_resp.json()
    except Exception as exc:
        logger.error("Failed to fetch userinfo from Google: %s", exc)
        target = "/profil" if action == "link" else ("/register" if action == "register" else "/login")
        return redirect_with_message(target, "Gagal mengambil data profil Google.")

    if not userinfo_resp.ok:
        target = "/profil" if action == "link" else ("/register" if action == "register" else "/login")
        return redirect_with_message(target, "Gagal memverifikasi akun Google.")

    google_id = str(userinfo.get("id") or "").strip()
    google_email = str(userinfo.get("email") or "").strip().lower()
    google_name = str(userinfo.get("name") or "").strip()

    if not google_id or not google_email:
        target = "/profil" if action == "link" else ("/register" if action == "register" else "/login")
        return redirect_with_message(target, "Data akun Google tidak memiliki ID atau Email.")

    store = get_store()

    # ── Action: LINK ACCOUNT ────────────────────────────────────────────────
    if action == "link":
        current_user = get_current_user(request)
        state_uid = state_data.get("user_id")
        if not current_user or int(current_user["id"]) != int(state_uid):
            return redirect_with_message("/login", "Sesi login tidak cocok saat menautkan akun.")

        try:
            store.link_google_account(int(current_user["id"]), google_id, google_email)
            return redirect_with_message("/profil", f"Akun Google ({google_email}) berhasil ditautkan!")
        except ValueError as exc:
            return redirect_with_message("/profil", str(exc))

    # ── Action: REGISTER DENGAN GOOGLE ─────────────────────────────────────
    if action == "register":
        # Check if already registered by google_id or email
        existing_user = store.get_user_by_google_id(google_id)
        if not existing_user and google_email:
            existing_user = store.get_user_by_email(google_email)
            if existing_user:
                try:
                    store.link_google_account(int(existing_user["id"]), google_id, google_email)
                except Exception:
                    pass

        # If already registered, seamless login!
        if existing_user:
            user_record = existing_user
        else:
            username = generate_unique_username(google_email, google_name, store)
            try:
                user_record = store.create_google_user(username, google_id, google_email)
            except ValueError as exc:
                return redirect_with_message("/register", str(exc))

        role = str(user_record.get("role") or "user").lower()
        user = {
            "id": int(user_record["id"]),
            "username": user_record["username"],
            "role": role,
            "session_version": max(1, int(user_record.get("session_version", 1) or 1)),
            "ui_theme": str(user_record.get("ui_theme") or DEFAULT_UI_THEME),
        }

        # Check MCP requirement:
        # Admin: NOT mandatory to enter MCP!
        if role == "admin":
            redirect = RedirectResponse(url="/admin", status_code=303)
            set_session_cookie(redirect, request, user)
            return redirect

        # Non-admin: MCP is mandatory!
        if is_mcp_connected(user["id"]):
            redirect = RedirectResponse(url="/dashboard", status_code=303)
            set_session_cookie(redirect, request, user)
            return redirect

        # Non-admin without MCP: show gating card
        token_info = store.get_xiaozhi_token_info(user["id"])
        response = render(
            request,
            "login.html",
            {
                "user": user,
                "error": None,
                "success": f"Pendaftaran Google berhasil! Halo @{user['username']}, silakan masukkan dan hubungkan endpoint MCP untuk mengakses Dashboard.",
                "active_mode": "mcp_gating",
                "active_page": "login",
                "mcp_pending": True,
                "mcp_token_preview": token_info.get("preview", "") if token_info else "",
            }
        )
        set_session_cookie(response, request, user)
        return response

    # ── Action: LOGIN DENGAN GOOGLE ────────────────────────────────────────
    # 1. Check if user exists by google_id
    user_record = store.get_user_by_google_id(google_id)

    # 2. Check if user exists by matching email
    if not user_record and google_email:
        user_record = store.get_user_by_email(google_email)
        if user_record:
            try:
                store.link_google_account(int(user_record["id"]), google_id, google_email)
            except Exception as exc:
                logger.warning("Could not auto-link Google account: %s", exc)

    # 3. If user is NOT registered yet, auto-register them seamlessly!
    if not user_record:
        username = generate_unique_username(google_email, google_name, store)
        try:
            user_record = store.create_google_user(username, google_id, google_email)
        except ValueError as exc:
            return redirect_with_message("/login", f"Gagal membuat akun Google: {exc}")

    # 4. User is registered / logged in, proceed with role & MCP check
    role = str(user_record.get("role") or "user").lower()
    user = {
        "id": int(user_record["id"]),
        "username": user_record["username"],
        "role": role,
        "session_version": max(1, int(user_record.get("session_version", 1) or 1)),
        "ui_theme": str(user_record.get("ui_theme") or DEFAULT_UI_THEME),
    }

    # Admin: never required to enter MCP!
    if role == "admin":
        redirect = RedirectResponse(url="/admin", status_code=303)
        set_session_cookie(redirect, request, user)
        return redirect

    # Non-admin: check MCP connection
    if is_mcp_connected(user["id"]):
        redirect = RedirectResponse(url="/dashboard", status_code=303)
        set_session_cookie(redirect, request, user)
        return redirect

    # Non-admin without MCP: show gating card
    token_info = store.get_xiaozhi_token_info(user["id"])
    response = render(
        request,
        "login.html",
        {
            "user": user,
            "error": None,
            "success": f"Masuk sebagai @{user['username']} berhasil! Silakan masukkan dan hubungkan endpoint MCP untuk mengakses Dashboard.",
            "active_mode": "mcp_gating",
            "active_page": "login",
            "mcp_pending": True,
            "mcp_token_preview": token_info.get("preview", "") if token_info else "",
        }
    )
    set_session_cookie(response, request, user)
    return response


@router.post("/unlink")
async def google_unlink(request: Request, csrf_token: str = Form(...)):
    """Unlink Google account from the current user."""
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")

    validate_csrf(request, csrf_token, user)

    store = get_store()
    try:
        store.unlink_google_account(int(user["id"]))
        return redirect_with_message("/profil", "Tautan akun Google berhasil diputuskan.")
    except ValueError as exc:
        return redirect_with_message("/profil", str(exc))
