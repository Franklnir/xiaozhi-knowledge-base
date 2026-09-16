import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from fastapi import HTTPException, Header, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired

from xiaozhi.config import (
    CSRF_MAX_AGE,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    csrf_serializer,
    session_serializer,
)
from xiaozhi.core.security import extract_bearer_token, validate_access_token, verify_password

# Templates will be set by main.py after app creation
templates: Optional[Jinja2Templates] = None
_store = None


def init_dependencies(templates_instance: Jinja2Templates, store) -> None:
    global templates, _store
    templates = templates_instance
    _store = store


def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """Get current user from session cookie OR JWT token."""
    # Try JWT token first (for mobile/API)
    auth_header = request.headers.get("Authorization")
    if auth_header:
        token = extract_bearer_token(auth_header)
        if token:
            user_info, error = validate_access_token(token)
            if user_info and not error:
                user = _store.get_user(user_info["user_id"])
                if user and user.get("username") == user_info.get("username"):
                    return user

    # Try session cookie (for web)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        payload = session_serializer.loads(token, max_age=SESSION_MAX_AGE)
        user = _store.get_user(int(payload["id"]))
        if (
            user
            and user["username"] == payload.get("username")
            and max(1, int(user.get("session_version", 1) or 1)) == max(1, int(payload.get("session_version", 1) or 1))
        ):
            return user
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None
    return None


def make_csrf_token(user: Optional[Dict[str, Any]]) -> str:
    return csrf_serializer.dumps(
        {
            "uid": int(user["id"]) if user else None,
            "nonce": secrets.token_urlsafe(16),
        }
    )


def validate_csrf(request: Request, token: str, user: Optional[Dict[str, Any]]) -> None:
    try:
        payload = csrf_serializer.loads(token, max_age=CSRF_MAX_AGE)
    except SignatureExpired as exc:
        raise HTTPException(status_code=403, detail="Form sudah kedaluwarsa. Muat ulang halaman.") from exc
    except BadSignature as exc:
        raise HTTPException(status_code=403, detail="Token keamanan form tidak valid.") from exc
    expected_uid = int(user["id"]) if user else None
    if payload.get("uid") != expected_uid:
        raise HTTPException(status_code=403, detail="Token keamanan form tidak cocok dengan sesi.")


def render(request: Request, name: str, context: Optional[Dict[str, Any]] = None, status_code: int = 200):
    user = context.get("user") if context else None
    if user is None:
        user = get_current_user(request)

    mcp_connected = False
    if user and isinstance(user, dict) and "id" in user:
        try:
            from xiaozhi.services.mcp_service import is_mcp_connected
            mcp_connected = bool(is_mcp_connected(int(user["id"])))
        except Exception:
            mcp_connected = False

    merged = {
        "user": user,
        "csrf_token": make_csrf_token(user),
        "mcp_connected": mcp_connected,
    }
    if context:
        merged.update(context)
        if "mcp_connected" not in context:
            merged["mcp_connected"] = mcp_connected
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=merged,
        status_code=status_code,
    )


def redirect_with_message(url: str, message: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url=f"{url}?{urlencode({'message': message})}", status_code=status_code)


def require_user(request: Request) -> Dict[str, Any]:
    user = get_current_user(request)
    if not user:
        # Check if browser request (Accept HTML) or API request (Accept JSON)
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            raise HTTPException(status_code=303, headers={"Location": "/login?message=Silakan+masuk+terlebih+dahulu."})
        raise HTTPException(status_code=401, detail="Login diperlukan.")
    return user


def require_admin(request: Request) -> Dict[str, Any]:
    user = get_current_user(request)
    if not user:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            raise HTTPException(status_code=303, headers={"Location": "/login?message=Silakan+masuk+terlebih+dahulu."})
        raise HTTPException(status_code=401, detail="Login diperlukan.")
    if user.get("role") != "admin":
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            raise HTTPException(status_code=303, headers={"Location": "/dashboard"})
        raise HTTPException(status_code=403, detail="Akses admin diperlukan.")
    return user


def generate_csrf(user: Optional[Dict[str, Any]]) -> str:
    return make_csrf_token(user)


def get_store():
    return _store
