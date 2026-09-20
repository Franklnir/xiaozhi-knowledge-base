import os
import mimetypes
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Request, HTTPException, Depends, Query, Path as FPath, status
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from xiaozhi.dependencies import get_current_user, require_user
from xiaozhi.marketplace.deps import (
    get_product_service,
    get_order_service,
    get_entitlement_service,
    get_wallet_service,
    get_chat_service,
)
from xiaozhi.marketplace.storage import storage_service
from xiaozhi.marketplace.security import verify_download_token

router = APIRouter(prefix="/api/v1/marketplace", tags=["Marketplace REST API"])


class OrderCreateRequest(BaseModel):
    product_id: str = Field(..., description="ID produk firmware yang akan dibeli")


class WithdrawalCreateRequest(BaseModel):
    amount: int = Field(..., ge=10000, description="Jumlah penarikan (minimal Rp 10.000)")
    destination_bank: str = Field(..., min_length=2, max_length=50)
    destination_account_name: str = Field(..., min_length=2, max_length=100)
    destination_account_number: str = Field(..., min_length=4, max_length=50)


class ChatSendMessageRequest(BaseModel):
    conversation_id: str
    message: str = Field(..., min_length=1, max_length=2000)


# ── CATALOG ──────────────────────────────────────────────────────────────────
@router.get("/products")
async def list_products(
    limit: int = Query(20, ge=1, le=50),
    cursor_time: Optional[str] = None,
    cursor_id: Optional[str] = None,
    q: Optional[str] = None,
):
    service = get_product_service()
    products = service.get_marketplace_list(
        limit=limit,
        cursor_time=cursor_time,
        cursor_id=cursor_id,
        search=q,
    )
    return {"success": True, "items": products, "count": len(products)}


@router.get("/products/{product_id}")
async def get_product(request: Request, product_id: str):
    user = get_current_user(request)
    uid = int(user["id"]) if user else None
    service = get_product_service()
    product = service.get_product_detail(product_id, current_user_id=uid)
    if not product:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "PRODUCT_NOT_FOUND", "message": "Produk tidak ditemukan."}}
        )
    return {"success": True, "data": product}


# ── ORDERS ───────────────────────────────────────────────────────────────────
@router.post("/orders")
async def create_order(request: Request, payload: OrderCreateRequest):
    user = require_user(request)
    service = get_order_service()
    try:
        order, checkout_url = await service.create_checkout_order(user, payload.product_id)
        return {
            "success": True,
            "data": {
                "order_id": str(order["id"]),
                "order_number": order["order_number"],
                "subtotal_amount": order["subtotal_amount"],
                "platform_fee_amount": order["platform_fee_amount"],
                "buyer_total_amount": order["buyer_total_amount"],
                "seller_net_amount": order["seller_net_amount"],
                "checkout_url": checkout_url,
            }
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "ORDER_CREATION_FAILED", "message": str(exc)}}
        )


# ── PURCHASES & DOWNLOADS ────────────────────────────────────────────────────
@router.get("/purchases")
async def list_purchases(request: Request):
    user = require_user(request)
    service = get_entitlement_service()
    purchases = service.get_user_purchases(int(user["id"]))
    return {"success": True, "items": purchases}


@router.get("/purchases/{purchase_id}/download")
async def download_firmware(request: Request, purchase_id: str):
    user = require_user(request)
    service = get_entitlement_service()
    try:
        dl_meta = service.authorize_download(purchase_id, int(user["id"]))
        return {"success": True, "download": dl_meta}
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "DOWNLOAD_NOT_ALLOWED", "message": str(exc)}}
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "DOWNLOAD_ERROR", "message": str(exc)}}
        )


# ── STORAGE LOCAL PROXIES (DUAL-MODE FALLBACK) ────────────────────────────────
@router.get("/images/{storage_key:path}")
async def serve_local_image(storage_key: str):
    """Safely serves local product images when S3 is not active."""
    path = storage_service.get_local_image_path(storage_key)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Gambar tidak ditemukan.")
    return FileResponse(path, media_type="image/webp")


@router.get("/storage/download/{token}")
async def serve_local_firmware_download(token: str, filename: Optional[str] = None):
    """
    Validates short-lived HMAC token and streams private firmware binary to authorized buyer.
    Direct path access is impossible because filenames use non-guessable UUIDs.
    """
    payload = verify_download_token(token)
    if not payload:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "TOKEN_EXPIRED_OR_INVALID", "message": "Link unduhan sudah kedaluwarsa atau tidak valid."}}
        )

    storage_key = payload.get("k", "")
    target_path = storage_service.get_local_firmware_path(storage_key)
    if not target_path or not target_path.exists():
        raise HTTPException(status_code=404, detail="File binary firmware tidak ditemukan di storage.")

    safe_filename = filename or "firmware.bin"
    return FileResponse(
        path=target_path,
        media_type="application/octet-stream",
        filename=safe_filename,
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )
