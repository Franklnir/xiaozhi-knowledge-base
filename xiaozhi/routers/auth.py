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
from xiaozhi.dependencies import (
    get_current_user,
    get_store,
    render,
    validate_csrf,
    redirect_with_message,
)

router = APIRouter()


def set_session_cookie(response: RedirectResponse, request: Request, user: dict) -> None:
    token = session_serializer.dumps({
        "id": user["id"],
        "username": user["username"],
        "session_version": user.get("session_version", 1),
    })
    # Check if HTTPS (consider proxy headers for HuggingFace/VPS)
    secure = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
        or os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
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
    return render(
        request,
        "login.html",
        {"user": None, "error": None, "success": None, "active_mode": active_mode},
    )


@router.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    auth_mode: str = Form("login"),
):
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
            redirect = RedirectResponse(url="/admin" if role == "admin" else "/dashboard", status_code=303)
            set_session_cookie(redirect, request, user)
            return redirect
        return render(
            request,
            "login.html",
            {
                "user": None,
                "error": "Username atau password salah.",
                "success": None,
                "active_mode": active_mode,
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
                "login_username": submitted_username if active_mode == "login" else "",
                "search_username": submitted_username if active_mode == "search" else "",
            },
            status_code=400,
        )


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard")
    return render(
        request,
        "register.html",
        {"user": None, "error": None, "success": None},
    )


@router.post("/register")
async def register_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    store = get_store()
    validate_csrf(request, csrf_token, None)
    try:
        user = store.create_user(username, password)
        # Redirect to login with success message
        return redirect_with_message("/login", "Akun berhasil dibuat! Silakan masuk.")
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


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
