import json
import logging
from typing import Optional, List
from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, status, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from xiaozhi.dependencies import render, get_current_user, require_user, redirect_with_message
from xiaozhi.marketplace.deps import (
    get_product_service,
    get_order_service,
    get_entitlement_service,
    get_wallet_service,
    get_marketplace_repo,
    get_chat_service,
)
from xiaozhi.config import MARKETPLACE_ADMIN_FEE_FLAT, MARKETPLACE_ADMIN_FEE_PERCENT
from xiaozhi.marketplace.services.bank_service import verify_account

logger = logging.getLogger("xiaozhi.marketplace.ui")
router = APIRouter(prefix="/firmware", tags=["Marketplace Web UI"])


# ── MARKETPLACE CATALOG ──────────────────────────────────────────────────────
@router.get("/marketplace", response_class=HTMLResponse)
async def marketplace_catalog(request: Request, q: Optional[str] = None):
    user = get_current_user(request)
    service = get_product_service()
    products = service.get_marketplace_list(limit=30, search=q)
    repo = get_marketplace_repo()
    return render(request, "marketplace/index.html", {
        "user": user,
        "page": "marketplace",
        "products": products,
        "search_query": q or "",
        "db_ready": repo.is_db_ready(),
    })


@router.get("/marketplace/{product_id_or_slug}", response_class=HTMLResponse)
async def marketplace_product_detail(request: Request, product_id_or_slug: str):
    user = get_current_user(request)
    uid = int(user["id"]) if user else None
    service = get_product_service()
    product = service.get_product_detail(product_id_or_slug, current_user_id=uid)
    if not product:
        raise HTTPException(status_code=404, detail="Produk firmware tidak ditemukan.")

    # Fee transparency calculation preview
    subtotal = int(product["price_amount"])
    platform_fee = int(MARKETPLACE_ADMIN_FEE_FLAT + int(subtotal * MARKETPLACE_ADMIN_FEE_PERCENT))
    platform_fee = min(platform_fee, subtotal)
    ppn_amount = int(round(platform_fee * 0.11))
    seller_net = subtotal - platform_fee

    # MCP connection check for buyer
    mcp_connected = False
    if user:
        from xiaozhi.services.mcp_service import mcp_status_payload
        token_saved = bool(user.get("mcp_token"))
        status_data = mcp_status_payload(int(user["id"]), token_saved=token_saved)
        mcp_connected = bool(status_data.get("connected"))

    return render(request, "marketplace/detail.html", {
        "user": user,
        "page": "marketplace",
        "product": product,
        "platform_fee": platform_fee,
        "ppn_amount": ppn_amount,
        "seller_net": seller_net,
        "mcp_connected": mcp_connected,
    })


# ── SELLER PRODUCTS ──────────────────────────────────────────────────────────
@router.get("/seller/products", response_class=HTMLResponse)
async def seller_products_page(request: Request, status: Optional[str] = None):
    return RedirectResponse(url="/profil?mode=marketplace&sub=products")


@router.post("/seller/products")
async def create_seller_product_action(
    request: Request,
    title: str = Form(...),
    short_description: str = Form(...),
    full_description: str = Form(...),
    price_amount: int = Form(...),
    doc_label: Optional[str] = Form(None),
    doc_url: Optional[str] = Form(None),
    images: List[UploadFile] = File(...),
    firmware_file: Optional[UploadFile] = File(None),
    stl_file: Optional[UploadFile] = File(None),
):
    user = require_user(request)
    service = get_product_service()

    # Read images bytes
    images_data = []
    for img in images[:3]:
        if img.filename:
            b = await img.read()
            if len(b) > 0:
                images_data.append(b)

    # Read firmware bytes if provided
    firmware_bytes = None
    firmware_filename = None
    if firmware_file and firmware_file.filename:
        b = await firmware_file.read()
        if len(b) > 0:
            firmware_bytes = b
            firmware_filename = firmware_file.filename

    # Read stl bytes if provided
    stl_bytes = None
    stl_filename = None
    if stl_file and stl_file.filename:
        b = await stl_file.read()
        if len(b) > 0:
            stl_bytes = b
            stl_filename = stl_file.filename

    links = []
    if doc_url and doc_url.strip():
        links.append({"label": (doc_label or "Dokumentasi").strip(), "url": doc_url.strip()})

    # Validasi kelengkapan data
    if not title or not title.strip():
        return redirect_with_message("/profil?mode=marketplace&sub=products", "Gagal: Judul produk firmware wajib diisi.")
    if not short_description or not short_description.strip():
        return redirect_with_message("/profil?mode=marketplace&sub=products", "Gagal: Deskripsi singkat firmware wajib diisi.")
    if not full_description or not full_description.strip():
        return redirect_with_message("/profil?mode=marketplace&sub=products", "Gagal: Deskripsi lengkap & panduan pinout wajib diisi.")
    if price_amount is None or price_amount < 0:
        return redirect_with_message("/profil?mode=marketplace&sub=products", "Gagal: Harga jual tidak valid.")
    if not images_data:
        return redirect_with_message("/profil?mode=marketplace&sub=products", "Gagal: Minimal 1 foto produk wajib diunggah.")
    if not firmware_bytes and not stl_bytes:
        return redirect_with_message(
            "/profil?mode=marketplace&sub=products",
            "Gagal: Wajib mengunggah minimal salah satu file: Binary Firmware (.bin) atau Model 3D (.stl)."
        )

    try:
        product = service.create_product(
            seller=user,
            title=title,
            short_description=short_description,
            full_description=full_description,
            price_amount=price_amount,
            images_data=images_data,
            firmware_bytes=firmware_bytes,
            firmware_filename=firmware_filename,
            stl_bytes=stl_bytes,
            stl_filename=stl_filename,
            links=links,
        )
        msg = "Produk firmware berhasil diterbitkan!" if user.get("role") == "admin" else "Draft produk firmware berhasil disimpan. Silakan ajukan untuk ditinjau."
        return redirect_with_message("/profil?mode=marketplace&sub=products", msg)
    except Exception as exc:
        logger.error("Gagal membuat produk seller: %s", exc)
        return redirect_with_message("/profil?mode=marketplace&sub=products", f"Gagal: {str(exc)}")


@router.post("/seller/products/{product_id}/submit")
async def submit_seller_product_action(request: Request, product_id: str):
    user = require_user(request)
    service = get_product_service()
    try:
        service.submit_for_review(product_id, int(user["id"]))
        return redirect_with_message("/profil?mode=marketplace&sub=products", "Produk berhasil diajukan untuk review admin.")
    except Exception as exc:
        return redirect_with_message("/profil?mode=marketplace&sub=products", f"Gagal mengajukan: {str(exc)}")


@router.get("/seller/products/{product_id}/edit", response_class=HTMLResponse)
async def edit_product_page(request: Request, product_id: str):
    user = require_user(request)
    service = get_product_service()
    product = service.get_product_detail(product_id, current_user_id=int(user["id"]))
    if not product:
        raise HTTPException(status_code=404, detail="Produk tidak ditemukan.")

    if product["seller_id"] != int(user["id"]) and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Akses ditolak.")

    return render(request, "marketplace/seller_product_edit.html", {
        "user": user,
        "page": "seller_products",
        "product": product,
    })


@router.post("/seller/products/{product_id}/edit")
async def update_seller_product_action(
    request: Request,
    product_id: str,
    title: str = Form(...),
    short_description: str = Form(...),
    full_description: str = Form(...),
    price_amount: int = Form(...),
    doc_label: Optional[str] = Form(None),
    doc_url: Optional[str] = Form(None),
    images: Optional[List[UploadFile]] = File(None),
    firmware_file: Optional[UploadFile] = File(None),
    stl_file: Optional[UploadFile] = File(None),
):
    user = require_user(request)
    service = get_product_service()

    # Read images bytes if uploaded
    images_data = []
    if images:
        for img in images[:3]:
            if img.filename:
                b = await img.read()
                if len(b) > 0:
                    images_data.append(b)

    # Read firmware bytes if new file uploaded
    firmware_bytes = None
    firmware_filename = None
    if firmware_file and firmware_file.filename:
        b = await firmware_file.read()
        if len(b) > 0:
            firmware_bytes = b
            firmware_filename = firmware_file.filename

    # Read stl bytes if new file uploaded
    stl_bytes = None
    stl_filename = None
    if stl_file and stl_file.filename:
        b = await stl_file.read()
        if len(b) > 0:
            stl_bytes = b
            stl_filename = stl_file.filename

    links = []
    if doc_url and doc_url.strip():
        links.append({"label": (doc_label or "Dokumentasi").strip(), "url": doc_url.strip()})

    try:
        service.update_product(
            product_id=product_id,
            user=user,
            title=title,
            short_description=short_description,
            full_description=full_description,
            price_amount=price_amount,
            images_data=images_data if images_data else None,
            firmware_bytes=firmware_bytes,
            firmware_filename=firmware_filename,
            stl_bytes=stl_bytes,
            stl_filename=stl_filename,
            links=links if (doc_url and doc_url.strip()) else None,
        )
        return redirect_with_message("/profil?mode=marketplace&sub=products", "Produk berhasil diperbarui.")
    except Exception as exc:
        return redirect_with_message(f"/firmware/seller/products/{product_id}/edit", f"Gagal memperbarui: {str(exc)}")


@router.post("/seller/products/{product_id}/delete")
async def delete_seller_product_action(request: Request, product_id: str):
    user = require_user(request)
    service = get_product_service()
    try:
        res = service.delete_product(product_id, user)
        action_text = "diarsipkan (karena memiliki histori transaksi)" if res.get("action") == "archived" else "berhasil dihapus"
        return redirect_with_message("/profil?mode=marketplace&sub=products", f"Produk {action_text}.")
    except Exception as exc:
        return redirect_with_message("/profil?mode=marketplace&sub=products", f"Gagal menghapus produk: {str(exc)}")



# ── BUYER PURCHASES & DOWNLOADS ──────────────────────────────────────────────
@router.get("/purchases", response_class=HTMLResponse)
async def buyer_purchases_page(request: Request):
    return RedirectResponse(url="/profil?mode=marketplace&sub=purchases")


@router.get("/purchases/{purchase_id}/download")
async def buyer_download_redirect(request: Request, purchase_id: str, asset: str = Query("bin")):
    user = require_user(request)
    service = get_entitlement_service()
    try:
        meta = service.authorize_download(purchase_id, int(user["id"]), asset_type=asset)
        return RedirectResponse(url=meta["download_url"], status_code=303)
    except Exception as exc:
        return redirect_with_message("/profil?mode=marketplace&sub=purchases", f"Gagal mengunduh: {str(exc)}")


# ── SELLER SALES & WITHDRAWALS ───────────────────────────────────────────────
@router.get("/seller/sales", response_class=HTMLResponse)
async def seller_sales_page(request: Request):
    return RedirectResponse(url="/profil?mode=marketplace&sub=sales")


@router.post("/seller/withdrawals")
async def request_withdrawal_action(
    request: Request,
    amount: int = Form(...),
    destination_bank: str = Form(...),
    destination_account_name: str = Form(...),
    destination_account_number: str = Form(...),
):
    user = require_user(request)
    service = get_wallet_service()
    try:
        inquiry = verify_account(destination_bank, destination_account_number, user)
        acc_name = destination_account_name.strip() or inquiry["account_name"]
        service.request_withdrawal(
            user_id=int(user["id"]),
            amount=amount,
            destination_bank=inquiry["short_name"],
            destination_account_name=acc_name,
            destination_account_number=inquiry["account_number"],
        )
        return redirect_with_message("/profil?mode=marketplace&sub=sales", f"Permintaan penarikan dana Rp {amount:,} berhasil diajukan ke {inquiry['short_name']} ({acc_name}).")
    except Exception as exc:
        return redirect_with_message("/profil?mode=marketplace&sub=sales", f"Penarikan gagal: {str(exc)}")


# ── SIMULATED CHECKOUT FLOW (ZERO-COST SANDBOX) ──────────────────────────────
@router.get("/checkout/simulate", response_class=HTMLResponse)
async def simulated_checkout_page(request: Request, order_number: str):
    user = require_user(request)
    repo = get_marketplace_repo()
    order = repo.get_order_by_number(order_number)
    if not order:
        raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan.")

    return render(request, "marketplace/checkout_simulate.html", {
        "user": user,
        "page": "checkout",
        "order": order,
    })


@router.post("/checkout/simulate/confirm")
async def confirm_simulated_checkout_action(request: Request, order_number: str = Form(...)):
    user = require_user(request)
    repo = get_marketplace_repo()
    order = repo.get_order_by_number(order_number)
    if not order:
        return redirect_with_message("/firmware/marketplace", "Pesanan tidak ditemukan.")

    # Finalize payment via repo
    import uuid
    evt_id = f"sim_{uuid.uuid4().hex[:12]}"
    repo.finalize_order_payment(
        order_number=order_number,
        provider="simulator",
        provider_event_id=evt_id,
        provider_reference=order_number,
        payload={"order_number": order_number, "status": "PAID", "simulated": True},
    )

    return redirect_with_message("/profil?mode=marketplace&sub=purchases", f"Pembayaran pesanan #{order_number} berhasil dikonfirmasi!")


# ── CHAT REDIRECTS & STARTER ────────────────────────────────────────────────
@router.get("/chats", response_class=HTMLResponse)
async def chats_redirect(request: Request):
    return RedirectResponse(url="/profil?mode=marketplace&sub=chats", status_code=303)


@router.get("/chats/start/{product_id_or_slug}")
async def start_chat_from_product(request: Request, product_id_or_slug: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url=f"/login?redirect=/firmware/marketplace/{product_id_or_slug}", status_code=303)

    product_service = get_product_service()
    product = product_service.get_product_detail(product_id_or_slug)
    if not product:
        return redirect_with_message("/firmware/marketplace", "Produk tidak ditemukan.")

    seller_id = int(product["seller_id"])
    buyer_id = int(user["id"])
    if seller_id == buyer_id:
        return redirect_with_message(f"/firmware/marketplace/{product_id_or_slug}", "Ini adalah produk milik Anda sendiri.")

    chat_service = get_chat_service()
    conv = chat_service.get_or_start_chat(str(product["id"]), seller_id, buyer_id)
    return RedirectResponse(url=f"/profil?mode=marketplace&sub=chats&conv_id={conv['id']}", status_code=303)

