import os
import logging
import mimetypes
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Request, HTTPException, Depends, Query, Path as FPath, status
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, RedirectResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("xiaozhi.marketplace.api")

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
from xiaozhi.marketplace.services.bank_service import (
    get_supported_banks,
    verify_account,
    find_bank_info,
)

router = APIRouter(prefix="/api/v1/marketplace", tags=["Marketplace REST API"])


class OrderCreateRequest(BaseModel):
    product_id: str = Field(..., description="ID produk firmware yang akan dibeli")


class WithdrawalCreateRequest(BaseModel):
    amount: int = Field(..., ge=10000, description="Jumlah penarikan (minimal Rp 10.000)")
    destination_bank: str = Field(..., min_length=2, max_length=50)
    destination_account_name: str = Field(..., min_length=2, max_length=100)
    destination_account_number: str = Field(..., min_length=4, max_length=50)


class BankInquiryRequest(BaseModel):
    bank_code: str = Field(..., min_length=1, max_length=50)
    account_number: str = Field(..., min_length=1, max_length=50)


class ChatSendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=3000)


class ChatStartRequest(BaseModel):
    product_id: str


class ChatShareProductRequest(BaseModel):
    product_id: str
    note: Optional[str] = None


# ── BANK INQUIRY & SUPPORTED LIST ───────────────────────────────────────────
@router.get("/supported-banks")
async def list_supported_banks():
    """Returns list of supported banks and e-wallets with formatting metadata and svg icons."""
    return {"success": True, "banks": get_supported_banks()}


@router.post("/bank-inquiry")
async def perform_bank_inquiry(request: Request, payload: BankInquiryRequest):
    """
    Validates bank account / e-wallet format and performs name inquiry.
    Returns verified account name and bank details.
    """
    user = get_current_user(request)
    try:
        result = verify_account(
            bank_code=payload.bank_code,
            account_number=payload.account_number,
            current_user=user,
        )
        return JSONResponse(status_code=200, content=result)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(exc)},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"Gagal memeriksa rekening: {str(exc)}"},
        )



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

    # 1. Syarat Pembelian: Wajib terhubung ke MCP
    from xiaozhi.services.mcp_service import mcp_status_payload
    token_saved = bool(user.get("mcp_token"))
    status_data = mcp_status_payload(int(user["id"]), token_saved=token_saved)
    if not status_data.get("connected") and user.get("role") != "admin":
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "MCP_REQUIRED", "message": "Syarat Pembelian: Akun atau board ESP32 Anda harus terhubung ke MCP terlebih dahulu sebelum dapat membeli produk firmware."}}
        )

    # 2. Fitur Beli Dinonaktifkan Sementara (Tahap Pengembangan)
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "FEATURE_UNDER_DEVELOPMENT", "message": "Fitur transaksi dan pembelian firmware saat ini sedang dalam tahap pengembangan & integrasi sistem pembayaran."}}
        )

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
async def download_firmware(request: Request, purchase_id: str, asset: str = "bin"):
    user = require_user(request)
    service = get_entitlement_service()
    try:
        dl_meta = service.authorize_download(purchase_id, int(user["id"]), asset_type=asset)
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


@router.get("/products/{product_id_or_slug}/stl-preview")
async def serve_stl_preview(request: Request, product_id_or_slug: str):
    """
    Serves the 3D STL file for client-side WebGL / Three.js interactive preview on the product detail page.
    Publicly viewable for published products; restricted to seller/admin for draft products.
    """
    product_service = get_product_service()
    product = product_service.get_product_detail(product_id_or_slug)
    if not product:
        raise HTTPException(status_code=404, detail="Produk tidak ditemukan.")

    # Access control: If not PUBLISHED, only seller or admin can view preview
    if product.get("status") != "PUBLISHED":
        user = get_current_user(request)
        is_admin = bool(user and str(user.get("role", "")).lower() == "admin")
        is_seller = bool(user and int(user.get("id", -1)) == int(product.get("seller_id", -2)))
        if not (is_admin or is_seller):
            raise HTTPException(status_code=403, detail="Akses preview model 3D dibatasi.")

    latest_ver = product.get("latest_version")
    if not latest_ver or not latest_ver.get("stl_storage_key"):
        raise HTTPException(status_code=404, detail="Produk ini tidak memiliki berkas 3D model STL.")

    stl_key = latest_ver["stl_storage_key"]
    filename = latest_ver.get("stl_original_filename") or "model.stl"

    if storage_service.has_s3:
        s3 = storage_service._get_s3()
        if s3:
            try:
                presigned_url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": storage_service.S3_BUCKET_PRIVATE, "Key": stl_key},
                    ExpiresIn=3600,
                )
                return RedirectResponse(presigned_url, status_code=307)
            except Exception as e:
                logger.error("Gagal generate presigned url untuk STL preview: %s", e)

    target_path = storage_service.get_local_firmware_path(stl_key)
    if not target_path or not target_path.exists():
        raise HTTPException(status_code=404, detail="File STL tidak ditemukan di storage server.")

    return FileResponse(
        path=target_path,
        media_type="model/stl",
        filename=filename,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/storage/download/{token}")
async def serve_local_firmware_download(token: str, filename: Optional[str] = None):
    """
    Validates short-lived HMAC token and streams private firmware binary or 3D STL file to authorized buyer.
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
        raise HTTPException(status_code=404, detail="File aset tidak ditemukan di storage.")

    is_stl = target_path.suffix.lower() == ".stl"
    safe_filename = filename or ("model.stl" if is_stl else "firmware.bin")
    media_type = "model/stl" if is_stl else "application/octet-stream"
    return FileResponse(
        path=target_path,
        media_type=media_type,
        filename=safe_filename,
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


# ── PRODUCT CHATS (1-ON-1 PRIVATE) ──────────────────────────────────────────
@router.get("/chats/unread-count")
async def get_chat_unread_count(request: Request):
    user = require_user(request)
    service = get_chat_service()
    count = service.get_total_unread_count(int(user["id"]))
    return {"success": True, "unread_count": count}


@router.get("/chats/conversations")
async def list_chat_conversations(request: Request):
    user = require_user(request)
    service = get_chat_service()
    convs = service.get_user_conversations(int(user["id"]))
    return {"success": True, "conversations": convs}


@router.post("/chats/start")
async def start_product_chat(request: Request, payload: ChatStartRequest):
    user = require_user(request)
    product_service = get_product_service()
    chat_service = get_chat_service()

    product = product_service.get_product_detail(payload.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Produk tidak ditemukan.")

    seller_id = int(product["seller_id"])
    buyer_id = int(user["id"])
    if seller_id == buyer_id:
        raise HTTPException(status_code=400, detail="Anda tidak dapat memulai percakapan pada produk milik Anda sendiri.")

    conv = chat_service.get_or_start_chat(str(product["id"]), seller_id, buyer_id)
    return {"success": True, "conversation": conv}


@router.get("/chats/conversations/{conversation_id}/messages")
async def get_chat_messages(request: Request, conversation_id: str):
    user = require_user(request)
    service = get_chat_service()
    try:
        conv = service.get_conversation(conversation_id, int(user["id"]))
        msgs = service.get_messages(conversation_id, int(user["id"]))
        return {"success": True, "conversation": conv, "messages": msgs}
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/chats/conversations/{conversation_id}/messages")
async def send_chat_message(request: Request, conversation_id: str, payload: ChatSendMessageRequest):
    user = require_user(request)
    service = get_chat_service()
    try:
        msg = service.send_message(conversation_id, int(user["id"]), payload.message)
        return {"success": True, "message": msg}
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/chats/my-shareable-products")
async def list_shareable_products(request: Request):
    user = require_user(request)
    service = get_chat_service()
    products = service.get_seller_shareable_products(int(user["id"]))
    return {"success": True, "products": products}


@router.post("/chats/conversations/{conversation_id}/share-product")
async def share_product_in_chat(request: Request, conversation_id: str, payload: ChatShareProductRequest):
    user = require_user(request)
    service = get_chat_service()
    try:
        msg = service.send_product_card(
            conversation_id=conversation_id,
            sender_id=int(user["id"]),
            product_id=payload.product_id,
            note=payload.note
        )
        return {"success": True, "message": msg}
    except (LookupError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

