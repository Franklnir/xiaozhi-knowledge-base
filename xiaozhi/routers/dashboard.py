from fastapi import APIRouter, HTTPException, Query, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from xiaozhi.services.sse_service import stream_material_indexing

from xiaozhi.config import DEFAULT_UI_THEME, LIVE_API_SOURCE_TYPE, UI_THEMES
from xiaozhi.core.utils import (
    build_api_materials,
    clean_multiline,
    clean_text,
    compact_text,
    is_live_api_category,
    preview_live_api_content,
    safe_api_label,
    summarize_api_materials,
    validate_external_api_url,
)
from xiaozhi.dependencies import (
    get_current_user,
    get_store,
    make_csrf_token,
    render,
    require_user,
    validate_csrf,
    redirect_with_message,
)
from xiaozhi.services.mcp_service import is_mcp_connected, mcp_status_payload, signal_mcp_reload

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")

    role = str(user.get("role") or "user").lower()
    # Admin is not required to enter MCP
    if role != "admin" and not is_mcp_connected(user["id"]):
        store = get_store()
        token_info = store.get_xiaozhi_token_info(user["id"])
        return render(
            request,
            "login.html",
            {
                "user": user,
                "error": "Akun Anda belum menghubungkan endpoint WebSocket MCP. Anda wajib menghubungkan MCP sebelum mengakses Dashboard.",
                "success": None,
                "active_mode": "mcp_gating",
                "active_page": "login",
                "mcp_pending": True,
                "mcp_token_preview": token_info.get("preview", "") if token_info else "",
            }
        )

    store = get_store()
    categories = store.list_categories(user["id"])
    materials = store.list_materials(user["id"])
    quota = store.user_quota(user["id"])
    token_info = store.get_xiaozhi_token_info(user["id"])
    mcp_status = mcp_status_payload(
        user["id"],
        token_saved=bool(token_info),
        token_preview=token_info.get("preview", "") if token_info else "",
        token_hash=token_info.get("token_hash", "") if token_info else "",
    )
    # Calculate stats
    stats = {
        "total": len(materials),
        "tugas": sum(1 for m in materials if "tugas" in m.get("category", "").lower()),
        "pengumuman": sum(1 for m in materials if "pengumuman" in m.get("category", "").lower()),
        "materi": sum(1 for m in materials if "materi" in m.get("category", "").lower()),
    }
    return render(
        request,
        "dashboard.html",
        {
            "user": user,
            "categories": categories,
            "materials": materials,
            "quota": quota,
            "stats": stats,
            "mcp_token_saved": bool(token_info),
            "mcp_token_preview": token_info.get("preview", "") if token_info else "",
            "mcp_status": mcp_status,
            "message": request.query_params.get("message", ""),
            "active_page": "dashboard",
        },
    )


@router.post("/add_material")
async def add_material(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    content: str = Form(...),
    keywords: str = Form(""),
    api_url: str = Form(""),
    csrf_token: str = Form(...),
):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)
    try:
        store.add_material(user["id"], title, category, content, keywords, api_url)
        signal_mcp_reload()
        return redirect_with_message("/dashboard", "Data materi berhasil ditambahkan.")
    except ValueError as exc:
        return redirect_with_message("/dashboard", f"Gagal: {exc}")


@router.post("/api/materials/stream-index")
async def api_materials_stream_index(request: Request):
    """Real-time SSE vectorization and material indexing progress stream."""
    user = require_user(request)
    store = get_store()

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        title = payload.get("title", "")
        category = payload.get("category", "")
        content = payload.get("content", "")
        keywords = payload.get("keywords", "")
        api_url = payload.get("api_url", "")
    else:
        form = await request.form()
        title = form.get("title", "")
        category = form.get("category", "")
        content = form.get("content", "")
        keywords = form.get("keywords", "")
        api_url = form.get("api_url", "")

    return StreamingResponse(
        stream_material_indexing(
            user_id=user["id"],
            title=str(title or ""),
            category=str(category or ""),
            content=str(content or ""),
            keywords=str(keywords or ""),
            api_url=str(api_url or ""),
            store=store,
            request=request,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )



@router.post("/delete_material/{material_id}")
async def delete_material(request: Request, material_id: int, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)
    deleted = store.delete_material(user["id"], material_id)
    if deleted:
        signal_mcp_reload()
    return redirect_with_message("/dashboard", "Materi dihapus." if deleted else "Materi tidak ditemukan.")


@router.post("/update_material/{material_id}")
async def update_material(
    request: Request,
    material_id: int,
    title: str = Form(...),
    category: str = Form(...),
    content: str = Form(...),
    keywords: str = Form(""),
    api_url: str = Form(""),
    csrf_token: str = Form(...),
):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)
    try:
        updated = store.update_material(user["id"], material_id, title, category, content, keywords, api_url)
        if updated:
            signal_mcp_reload()
        return redirect_with_message("/dashboard", "Materi diperbarui." if updated else "Materi tidak ditemukan.")
    except ValueError as exc:
        return redirect_with_message("/dashboard", f"Gagal: {exc}")


@router.post("/add_category")
async def add_category(request: Request, name: str = Form(...), csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)
    try:
        store.add_category(user["id"], name)
        return redirect_with_message("/dashboard", f"Kategori '{name}' ditambahkan.")
    except ValueError as exc:
        return redirect_with_message("/dashboard", f"Gagal: {exc}")


@router.post("/delete_category/{cat_id}")
async def delete_category(request: Request, cat_id: int, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)
    try:
        deleted = store.delete_category(user["id"], cat_id)
        return redirect_with_message("/dashboard", "Kategori dihapus." if deleted else "Kategori tidak ditemukan.")
    except ValueError as exc:
        return redirect_with_message("/dashboard", f"Gagal: {exc}")


@router.post("/api/user/theme")
async def set_user_theme(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Login diperlukan"}, status_code=401)
    store = get_store()
    body = await request.json()
    theme = str(body.get("theme", "")).strip()
    try:
        store.set_user_theme(user["id"], theme)
        return JSONResponse({"success": True, "theme": theme})
    except ValueError as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=400)


@router.get("/api/user/features")
async def get_user_features(request: Request):
    user = require_user(request)
    store = get_store()
    features = store.get_user_features(user["id"])
    return {"success": True, "features": features}
