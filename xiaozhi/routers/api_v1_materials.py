"""
API v1 Materials endpoints for mobile and API clients.
All responses follow {success, data, message} format.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from xiaozhi.dependencies import get_current_user, get_store, require_user
from xiaozhi.services.mcp_service import is_mcp_connected, mcp_status_payload, signal_mcp_reload

router = APIRouter(prefix="/api/v1/materials", tags=["API v1 Materials"])


# ── Response Models ────────────────────────────────────────────────────────

class ApiResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None
    message: str = "OK"


class MaterialCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=160)
    category: str = Field(..., min_length=2, max_length=80)
    content: str = Field(..., min_length=5)
    keywords: str = Field("", max_length=300)
    api_url: str = Field("")


class MaterialUpdate(BaseModel):
    title: str = Field(..., min_length=3, max_length=160)
    category: str = Field(..., min_length=2, max_length=80)
    content: str = Field(..., min_length=5)
    keywords: str = Field("", max_length=300)
    api_url: str = Field("")


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("", response_model=ApiResponse)
async def list_materials(request: Request, category: Optional[str] = None):
    """List all materials for the authenticated user."""
    user = require_user(request)
    store = get_store()
    materials = store.list_materials(user["id"])

    if category:
        materials = [m for m in materials if m.get("category", "").lower() == category.lower()]

    return ApiResponse(
        success=True,
        data={"materials": materials, "count": len(materials)},
        message=f"Ditemukan {len(materials)} materi."
    )


@router.post("", response_model=ApiResponse, status_code=201)
async def create_material(request: Request, body: MaterialCreate):
    """Create a new material."""
    user = require_user(request)
    store = get_store()

    try:
        store.add_material(user["id"], body.title, body.category, body.content, body.keywords, body.api_url)
        signal_mcp_reload()
        return ApiResponse(
            success=True,
            data={"title": body.title},
            message="Materi berhasil ditambahkan."
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=ApiResponse(success=False, message=str(exc)))


@router.get("/{material_id}", response_model=ApiResponse)
async def get_material(request: Request, material_id: int):
    """Get a specific material by ID."""
    user = require_user(request)
    store = get_store()

    material = store.get_material(user["id"], material_id)
    if not material:
        raise HTTPException(status_code=404, detail=ApiResponse(success=False, message="Materi tidak ditemukan."))

    return ApiResponse(
        success=True,
        data={"material": material},
        message="OK"
    )


@router.put("/{material_id}", response_model=ApiResponse)
async def update_material(request: Request, material_id: int, body: MaterialUpdate):
    """Update an existing material."""
    user = require_user(request)
    store = get_store()

    try:
        updated = store.update_material(user["id"], material_id, body.title, body.category, body.content, body.keywords, body.api_url)
        if not updated:
            raise HTTPException(status_code=404, detail=ApiResponse(success=False, message="Materi tidak ditemukan."))

        signal_mcp_reload()
        return ApiResponse(
            success=True,
            data={"material_id": material_id},
            message="Materi berhasil diperbarui."
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=ApiResponse(success=False, message=str(exc)))


@router.delete("/{material_id}", response_model=ApiResponse)
async def delete_material(request: Request, material_id: int):
    """Delete a material."""
    user = require_user(request)
    store = get_store()

    deleted = store.delete_material(user["id"], material_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=ApiResponse(success=False, message="Materi tidak ditemukan."))

    signal_mcp_reload()
    return ApiResponse(
        success=True,
        data={"material_id": material_id},
        message="Materi berhasil dihapus."
    )


@router.get("/search", response_model=ApiResponse)
async def search_materials(request: Request, q: str = Query("", max_length=200)):
    """Search materials by keyword."""
    user = require_user(request)
    store = get_store()

    results = store.search_materials(user["id"], q, limit=10)

    return ApiResponse(
        success=True,
        data={"results": results, "count": len(results), "query": q},
        message=f"Ditemukan {len(results)} hasil."
    )
