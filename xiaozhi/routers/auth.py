import os
import secrets
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired

from xiaozhi.config import (
    DEFAULT_UI_THEME,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    session_serializer,
)
from xiaozhi.core.security import normalize_username, hash_password, verify_password
from xiaozhi.core.rate_limiter import enforce_predefined_limit
from xiaozhi.dependencies import (
    get_current_user,
    get_store,
    render,
    validate_csrf,
    redirect_with_message,
    make_csrf_token,
)

router = APIRouter()


def set_session_cookie(response: RedirectResponse, request: Request, user: dict) -> None:
    token = session_serializer.dumps({
        "id": user["id"],
        "username": user["username"],
        "session_version": user.get("session_version", 1),
    })
    # HuggingFace Spaces: always secure, no domain restriction
    is_hf = bool(os.getenv("SPACE_ID"))
    secure = is_hf or request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="none" if is_hf else "lax",
        path="/",
    )


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if user:
        if user.get("role") == "admin":
            return RedirectResponse(url="/admin")
        return RedirectResponse(url="/dashboard")
    return RedirectResponse(url="/login")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, mode: str = Query("login")):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard")
    active_mode = mode if mode in {"login", "search", "register"} else "login"
    error = request.query_params.get("error")
    success = request.query_params.get("message") or request.query_params.get("success")
    return render(
        request,
        "login.html",
        {"user": None, "error": error, "success": success, "active_mode": active_mode, "active_page": "login"},
    )


@router.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    auth_mode: str = Form("login"),
):
    from xiaozhi.services.mcp_service import is_mcp_connected
    enforce_predefined_limit(request, "login")
    store = get_store()
    validate_csrf(request, csrf_token, None)
    active_mode = "search" if auth_mode == "search" else "login"
    submitted_username = (username or "").strip().lower()
    try:
        user_record = store.get_user_by_username(username)
        if user_record and verify_password(password, user_record.get("password_hash", "")):
            role = str(user_record.get("role") or "user").lower()
            user = {
                "id": int(user_record["id"]),
                "username": user_record["username"],
                "role": role,
                "session_version": max(1, int(user_record.get("session_version", 1) or 1)),
                "ui_theme": str(user_record.get("ui_theme") or DEFAULT_UI_THEME),
            }
            # Admin always bypasses MCP gating
            if role == "admin" or is_mcp_connected(user["id"]):
                redirect = RedirectResponse(url="/admin" if role == "admin" else "/dashboard", status_code=303)
                set_session_cookie(redirect, request, user)
                return redirect

            # If user has not connected MCP, show MCP gating panel
            token_info = store.get_xiaozhi_token_info(user["id"])
            response = render(
                request,
                "login.html",
                {
                    "user": user,
                    "error": None,
                    "success": "Sesi aktif. Masukkan endpoint WebSocket MCP untuk melanjutkan ke Dashboard.",
                    "active_mode": "mcp_gating",
                    "active_page": "login",
                    "mcp_pending": True,
                    "mcp_token_preview": token_info.get("preview", "") if token_info else "",
                }
            )
            set_session_cookie(response, request, user)
            return response

        return render(
            request,
            "login.html",
            {
                "user": None,
                "error": "Username atau password salah.",
                "success": None,
                "active_mode": active_mode,
                "active_page": "login",
                "login_username": submitted_username if active_mode == "login" else "",
                "search_username": submitted_username if active_mode == "search" else "",
            },
            status_code=400,
        )
    except ValueError:
        return render(
            request,
            "login.html",
            {
                "user": None,
                "error": "Username atau password salah.",
                "success": None,
                "active_mode": active_mode,
                "active_page": "login",
                "login_username": submitted_username if active_mode == "login" else "",
                "search_username": submitted_username if active_mode == "search" else "",
            },
            status_code=400,
        )


@router.post("/api/auth/web-login")
async def web_login_api(request: Request):
    from fastapi.responses import JSONResponse
    from xiaozhi.services.mcp_service import is_mcp_connected
    enforce_predefined_limit(request, "login")
    body = await request.json()
    username = str(body.get("username", "")).strip().lower()
    password = str(body.get("password", ""))
    csrf_token = str(body.get("csrf_token", ""))

    validate_csrf(request, csrf_token, None)
    store = get_store()
    user_record = store.get_user_by_username(username)
    if not user_record or not verify_password(password, user_record.get("password_hash", "")):
        return JSONResponse({"success": False, "message": "Username atau password salah."}, status_code=401)

    role = str(user_record.get("role") or "user").lower()
    user_id = int(user_record["id"])
    user = {
        "id": user_id,
        "username": user_record["username"],
        "role": role,
        "session_version": max(1, int(user_record.get("session_version", 1) or 1)),
        "ui_theme": str(user_record.get("ui_theme") or DEFAULT_UI_THEME),
    }

    token_info = store.get_xiaozhi_token_info(user_id)
    mcp_ok = is_mcp_connected(user_id)

    new_csrf = make_csrf_token(user)
    if role == "admin" or mcp_ok:
        res = JSONResponse({
            "success": True,
            "mcp_required": False,
            "csrf_token": new_csrf,
            "redirect_url": "/admin" if role == "admin" else "/dashboard"
        })
        set_session_cookie(res, request, user)
        return res
    else:
        res = JSONResponse({
            "success": True,
            "mcp_required": True,
            "csrf_token": new_csrf,
            "token_saved": bool(token_info),
            "token_preview": token_info.get("preview", "") if token_info else "",
            "message": "Koneksi MCP belum terhubung. Silakan hubungkan endpoint WebSocket MCP."
        })
        set_session_cookie(res, request, user)
        return res


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard")
    error = request.query_params.get("error")
    success = request.query_params.get("message") or request.query_params.get("success")
    return render(
        request,
        "register.html",
        {"user": None, "error": error, "success": success, "active_page": "login"},
    )


@router.post("/register")
async def register_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    enforce_predefined_limit(request, "register")
    store = get_store()
    validate_csrf(request, csrf_token, None)
    try:
        user_record = store.create_user(username, password)
        user_id = int(user_record["id"])
        user = {
            "id": user_id,
            "username": user_record["username"],
            "role": "user",
            "session_version": 1,
            "ui_theme": DEFAULT_UI_THEME,
        }
        # Render register page with MCP input section smoothly shown below
        response = render(
            request,
            "register.html",
            {
                "user": user,
                "error": None,
                "success": "Akun berhasil dibuat! Silakan masukkan dan hubungkan endpoint MCP di bawah ini.",
                "active_page": "login",
                "mcp_pending": True,
                "registered_username": username,
            }
        )
        set_session_cookie(response, request, user)
        return response
    except ValueError as exc:
        return render(
            request,
            "register.html",
            {
                "user": None,
                "error": str(exc),
                "success": None,
            },
            status_code=400,
        )


@router.post("/api/auth/web-register")
async def web_register_api(request: Request):
    from fastapi.responses import JSONResponse
    enforce_predefined_limit(request, "register")
    body = await request.json()
    username = str(body.get("username", "")).strip().lower()
    password = str(body.get("password", ""))
    csrf_token = str(body.get("csrf_token", ""))

    validate_csrf(request, csrf_token, None)
    store = get_store()
    try:
        user_record = store.create_user(username, password)
        user = {
            "id": int(user_record["id"]),
            "username": user_record["username"],
            "role": "user",
            "session_version": 1,
            "ui_theme": DEFAULT_UI_THEME,
        }
        new_csrf = make_csrf_token(user)
        res = JSONResponse({
            "success": True,
            "message": "Akun berhasil dibuat! Silakan hubungkan endpoint MCP.",
            "mcp_required": True,
            "username": username,
            "csrf_token": new_csrf,
        })
        set_session_cookie(res, request, user)
        return res
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse({"success": False, "message": "Gagal membuat akun."}, status_code=500)


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
