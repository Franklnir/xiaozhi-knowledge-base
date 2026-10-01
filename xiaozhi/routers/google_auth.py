"""
Google OAuth 2.0 Router for Xiaozhi Indonesia.
Supports:
1. Login & Register with Google.
2. Link Google Account from user profile.
3. Unlink Google Account with confirmation and CSRF verification.
"""
import base64
import hashlib
import logging
import os
import re
import secrets
from typing import Optional
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import RedirectResponse
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token as google_id_token
from itsdangerous import BadSignature, SignatureExpired

from xiaozhi.config import (
    DEFAULT_UI_THEME,
    GOOGLE_AUTH_ENABLED,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    IS_PRODUCTION,
    google_oauth_serializer,
)
from xiaozhi.core.security import create_token_pair, normalize_username
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

OAUTH_COOKIE_PATH = "/api/auth/google"
# These cookies are intentionally scoped to the OAuth endpoint.  __Secure-
# requires HTTPS without imposing the Path=/ rule of the __Host- prefix.
OAUTH_STATE_COOKIE = "__Secure-google_oauth_state" if IS_PRODUCTION else "google_oauth_state"
OAUTH_PKCE_COOKIE = "__Secure-google_oauth_pkce" if IS_PRODUCTION else "google_oauth_pkce"
MOBILE_SOURCES = {"mobile", "mobile_app", "app"}


def _secure_cookie(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    return IS_PRODUCTION or request.url.scheme == "https" or forwarded_proto == "https"


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _set_oauth_cookies(response: RedirectResponse, request: Request, state_nonce: str, verifier: str) -> None:
    options = {
        "max_age": 600,
        "httponly": True,
        "secure": _secure_cookie(request),
        "samesite": "lax",
        "path": OAUTH_COOKIE_PATH,
    }
    response.set_cookie(OAUTH_STATE_COOKIE, state_nonce, **options)
    response.set_cookie(OAUTH_PKCE_COOKIE, verifier, **options)


def _clear_oauth_cookies(response: RedirectResponse) -> None:
    options = {
        "path": OAUTH_COOKIE_PATH,
        "secure": IS_PRODUCTION,
        "httponly": True,
        "samesite": "lax",
    }
    response.delete_cookie(OAUTH_STATE_COOKIE, **options)
    response.delete_cookie(OAUTH_PKCE_COOKIE, **options)


def _normalize_source(source: str) -> str:
    return source if source in MOBILE_SOURCES else "web"


def _build_google_authorization(request: Request, action: str, source: str, user_id: Optional[int] = None) -> RedirectResponse:
    state_nonce = secrets.token_urlsafe(24)
    oidc_nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = _pkce_challenge(verifier)
    state_payload = {
        "action": action,
        "source": _normalize_source(source),
        "nonce": state_nonce,
        "oidc_nonce": oidc_nonce,
        "pkce_challenge": challenge,
    }
    if user_id is not None:
        state_payload["user_id"] = int(user_id)
    state = google_oauth_serializer.dumps(state_payload)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": get_google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": oidc_nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    }
    response = RedirectResponse(
        url=f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}",
        status_code=303,
    )
    _set_oauth_cookies(response, request, state_nonce, verifier)
    return response


def get_google_redirect_uri(request: Request) -> str:
    """Determine the valid Google OAuth redirect URI matching Google Cloud Console configuration."""
    # Detect host and protocol from request
    forwarded_host = request.headers.get("x-forwarded-host")
    host = forwarded_host or request.headers.get("host") or ""
    hostname = host.split(",", 1)[0].strip().split(":", 1)[0].lower()
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"

    # Match against authorized redirect URIs in Google Cloud Console:
    # - https://xiaozhiscig.biz.id/api/auth/google/callback
    # - http://localhost:8000/api/auth/google/callback
    # - http://127.0.0.1:8000/api/auth/google/callback
    if hostname == "127.0.0.1":
        return "http://127.0.0.1:8000/api/auth/google/callback"
    if hostname == "localhost":
        return "http://localhost:8000/api/auth/google/callback"
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    if hostname in {"xiaozhiscig.biz.id", "www.xiaozhiscig.biz.id"} or os.getenv("ENVIRONMENT") == "production":
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
async def google_login(
    request: Request,
    intent: str = Query("login"),
    source: str = Query("web"),
):
    """Initiate Google OAuth flow for Login, Register, or Link."""
    if intent == "link":
        return await google_link(request=request, source=source)

    if not GOOGLE_AUTH_ENABLED or not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        logger.error("Google OAuth credentials not configured.")
        if source in ("mobile", "mobile_app", "app"):
            return RedirectResponse(url=f"espbridge://oauth/callback?error={urlencode({'msg': 'Integrasi Google belum dikonfigurasi di server.'})}", status_code=303)
        return redirect_with_message("/login", "Integrasi Google belum dikonfigurasi di server.")

    valid_intent = "register" if intent == "register" else "login"
    return _build_google_authorization(request, valid_intent, source)


@router.get("/link")
async def google_link(
    request: Request,
    source: str = Query("web"),
):
    """Initiate Google OAuth flow to link Google account to current logged-in user."""
    user = get_current_user(request)

    if not user:
        if source in ("mobile", "mobile_app", "app"):
            return RedirectResponse(
                url=f"espbridge://oauth/callback?error={urlencode({'msg': 'Sesi login tidak valid atau telah berakhir.'})}",
                status_code=303
            )
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu untuk menautkan Google.")

    if not GOOGLE_AUTH_ENABLED or not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        if source in ("mobile", "mobile_app", "app"):
            return RedirectResponse(
                url=f"espbridge://oauth/callback?error={urlencode({'msg': 'Integrasi Google belum dikonfigurasi di server.'})}",
                status_code=303
            )
        return redirect_with_message("/profil", "Integrasi Google belum dikonfigurasi di server.")

    return _build_google_authorization(request, "link", source, int(user["id"]))


@router.get("/callback")
async def google_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Handle OAuth 2.0 callback from Google."""
    if not GOOGLE_AUTH_ENABLED:
        return redirect_with_message("/login", "Integrasi Google sedang dinonaktifkan.")
    if error:
        logger.warning("Google OAuth error callback: %s", error)
        response = redirect_with_message("/login", "Autentikasi Google dibatalkan.")
        _clear_oauth_cookies(response)
        return response

    if not code or not state:
        response = redirect_with_message("/login", "Permintaan autentikasi Google tidak lengkap.")
        _clear_oauth_cookies(response)
        return response

    # Validate state parameter
    try:
        state_data = google_oauth_serializer.loads(state, max_age=600)
    except (SignatureExpired, BadSignature) as exc:
        logger.warning("Invalid or expired Google OAuth state: %s", exc)
        response = redirect_with_message("/login", "Sesi autentikasi Google kedaluwarsa. Silakan coba lagi.")
        _clear_oauth_cookies(response)
        return response
    if not isinstance(state_data, dict):
        response = redirect_with_message("/login", "Sesi autentikasi Google tidak valid.")
        _clear_oauth_cookies(response)
        return response

    action = str(state_data.get("action") or "")
    if action not in {"login", "register", "link"}:
        response = redirect_with_message("/login", "Aksi autentikasi Google tidak valid.")
        _clear_oauth_cookies(response)
        return response
    is_mobile = state_data.get("source") in MOBILE_SOURCES
    state_nonce = str(state_data.get("nonce") or "")
    cookie_nonce = request.cookies.get(OAUTH_STATE_COOKIE, "")
    if not state_nonce or not cookie_nonce or not secrets.compare_digest(state_nonce, cookie_nonce):
        logger.warning("Google OAuth state cookie mismatch")
        response = redirect_with_message("/login", "Sesi autentikasi Google tidak valid. Silakan coba lagi.")
        _clear_oauth_cookies(response)
        return response

    verifier = request.cookies.get(OAUTH_PKCE_COOKIE, "")
    expected_challenge = str(state_data.get("pkce_challenge") or "")
    oidc_nonce = str(state_data.get("oidc_nonce") or "")
    if (
        not verifier
        or not expected_challenge
        or not secrets.compare_digest(_pkce_challenge(verifier), expected_challenge)
        or not oidc_nonce
    ):
        logger.warning("Google OAuth PKCE or OIDC nonce is missing or invalid")
        response = redirect_with_message("/login", "Sesi autentikasi Google tidak valid. Silakan coba lagi.")
        _clear_oauth_cookies(response)
        return response

    def respond_error(msg: str, target: str = "/login"):
        if is_mobile:
            response = RedirectResponse(url=f"espbridge://oauth/callback?error={urlencode({'msg': msg})}", status_code=303)
        else:
            response = redirect_with_message(target, msg)
        _clear_oauth_cookies(response)
        return response

    def mobile_success_response(u: dict, r: str) -> RedirectResponse:
        token_pair = create_token_pair(u["id"], u["username"], r, u.get("session_version", 1))
        cb_params = {
            "access_token": token_pair["access_token"],
            "refresh_token": token_pair["refresh_token"],
            "username": u["username"],
            "email": u.get("google_email") or google_email or "",
            "role": r,
            "user_id": str(u["id"]),
        }
        response = RedirectResponse(url=f"espbridge://oauth/callback#{urlencode(cb_params)}", status_code=303)
        _clear_oauth_cookies(response)
        return response

    # Exchange authorization code for tokens
    redirect_uri = get_google_redirect_uri(request)
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }

    try:
        token_resp = requests.post(token_url, data=payload, timeout=10)
        token_data = token_resp.json()
    except Exception as exc:
        logger.error("Failed to exchange code with Google: %s", exc)
        target = "/profil" if action == "link" else ("/register" if action == "register" else "/login")
        return respond_error("Gagal menghubungi server Google. Coba lagi.", target)

    if not isinstance(token_data, dict) or not token_resp.ok or "id_token" not in token_data:
        if not isinstance(token_data, dict):
            token_data = {}
        error_code = str(token_data.get("error") or "unknown_error")[:80]
        logger.error("Google token exchange failed (status=%s, error=%s)", token_resp.status_code, error_code)
        target = "/profil" if action == "link" else ("/register" if action == "register" else "/login")
        return respond_error("Google menolak permintaan login. Silakan coba lagi.", target)

    # Verify signature, audience, issuer and expiry using Google's maintained
    # certificate verifier, then bind the ID token to this browser flow by nonce.
    try:
        claims = google_id_token.verify_oauth2_token(
            token_data["id_token"],
            google_auth_requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=30,
        )
    except Exception as exc:
        logger.warning("Google ID token verification failed: %s", type(exc).__name__)
        target = "/profil" if action == "link" else ("/register" if action == "register" else "/login")
        return respond_error("Gagal memverifikasi akun Google.", target)

    token_nonce = str(claims.get("nonce") or "")
    google_id = str(claims.get("sub") or "").strip()
    google_email = str(claims.get("email") or "").strip().lower()
    google_name = str(claims.get("name") or "").strip()
    email_verified = claims.get("email_verified") is True or claims.get("email_verified") == "true"

    if (
        not token_nonce
        or not secrets.compare_digest(token_nonce, oidc_nonce)
        or not google_id
        or not google_email
        or not email_verified
    ):
        target = "/profil" if action == "link" else ("/register" if action == "register" else "/login")
        return respond_error("Identitas akun Google tidak valid atau email belum diverifikasi.", target)

    store = get_store()

    # ── Action: LINK ACCOUNT ────────────────────────────────────────────────
    if action == "link":
        state_uid = state_data.get("user_id")
        if not state_uid:
            return respond_error("Data sesi tautan Google tidak valid.", "/login")

        current_user = get_current_user(request)
        if not current_user or int(current_user["id"]) != int(state_uid):
            return respond_error("Sesi login tidak cocok saat menautkan akun.", "/login")

        user_to_link = current_user
        if not user_to_link:
            return respond_error("Pengguna tidak ditemukan untuk menautkan akun.", "/login")

        try:
            store.link_google_account(int(user_to_link["id"]), google_id, google_email)
        except ValueError as exc:
            return respond_error(str(exc), "/profil")

        if is_mobile:
            cb_params = {
                "action": "link",
                "status": "success",
                "email": google_email,
                "msg": f"Akun Google ({google_email}) berhasil ditautkan!",
            }
            response = RedirectResponse(url=f"espbridge://oauth/callback#{urlencode(cb_params)}", status_code=303)
            _clear_oauth_cookies(response)
            return response

        redirect = redirect_with_message("/profil", f"Akun Google ({google_email}) berhasil ditautkan!")
        _clear_oauth_cookies(redirect)
        set_session_cookie(redirect, request, user_to_link)
        return redirect

    # ── Action: REGISTER DENGAN GOOGLE ─────────────────────────────────────
    if action == "register":
        # Only an already linked Google subject may sign in directly.  A matching
        # email alone is not proof that this browser may take over a local account.
        existing_user = store.get_user_by_google_id(google_id)
        if not existing_user and store.get_user_by_email(google_email):
            return respond_error(
                "Email ini sudah dipakai. Masuk dengan password lalu tautkan Google dari halaman Profil.",
                "/login",
            )

        # If already registered, seamless login!
        if existing_user:
            user_record = existing_user
        else:
            username = generate_unique_username(google_email, google_name, store)
            try:
                user_record = store.create_google_user(username, google_id, google_email)
            except ValueError as exc:
                return respond_error(str(exc), "/register")

        role = str(user_record.get("role") or "user").lower()
        user = {
            "id": int(user_record["id"]),
            "username": user_record["username"],
            "role": role,
            "session_version": max(1, int(user_record.get("session_version", 1) or 1)),
            "ui_theme": str(user_record.get("ui_theme") or DEFAULT_UI_THEME),
        }

        if is_mobile:
            return mobile_success_response(user, role)

        # Check MCP requirement:
        # Admin: NOT mandatory to enter MCP!
        if role == "admin":
            redirect = RedirectResponse(url="/admin", status_code=303)
            _clear_oauth_cookies(redirect)
            set_session_cookie(redirect, request, user)
            return redirect

        # Non-admin: MCP is mandatory!
        if is_mcp_connected(user["id"]):
            redirect = RedirectResponse(url="/dashboard", status_code=303)
            _clear_oauth_cookies(redirect)
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
        _clear_oauth_cookies(response)
        set_session_cookie(response, request, user)
        return response

    # ── Action: LOGIN DENGAN GOOGLE ────────────────────────────────────────
    # 1. Check if user exists by google_id
    user_record = store.get_user_by_google_id(google_id)

    # Never auto-link by email. Account linking requires an authenticated local
    # session and a separate OAuth state bound to that user ID.
    if not user_record:
        if store.get_user_by_email(google_email):
            return respond_error(
                "Akun Google belum ditautkan. Masuk dengan password lalu tautkan dari halaman Profil.",
                "/login",
            )
        # Seamless registration if account does not exist yet
        username = generate_unique_username(google_email, google_name, store)
        try:
            user_record = store.create_google_user(username, google_id, google_email)
        except ValueError as exc:
            return respond_error(str(exc), "/login")

    # User is registered / linked, proceed with role & MCP check.
    role = str(user_record.get("role") or "user").lower()
    user = {
        "id": int(user_record["id"]),
        "username": user_record["username"],
        "role": role,
        "session_version": max(1, int(user_record.get("session_version", 1) or 1)),
        "ui_theme": str(user_record.get("ui_theme") or DEFAULT_UI_THEME),
    }

    if is_mobile:
        return mobile_success_response(user, role)

    # Admin: never required to enter MCP!
    if role == "admin":
        redirect = RedirectResponse(url="/admin", status_code=303)
        _clear_oauth_cookies(redirect)
        set_session_cookie(redirect, request, user)
        return redirect

    # Non-admin: check MCP connection
    if is_mcp_connected(user["id"]):
        redirect = RedirectResponse(url="/dashboard", status_code=303)
        _clear_oauth_cookies(redirect)
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
    _clear_oauth_cookies(response)
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
