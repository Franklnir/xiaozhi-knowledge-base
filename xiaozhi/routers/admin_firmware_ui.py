import logging
from fastapi import APIRouter, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse

from xiaozhi.dependencies import render, require_admin, redirect_with_message
from typing import Optional
from xiaozhi.marketplace.deps import get_approval_service, get_wallet_service, get_marketplace_repo, get_product_service

logger = logging.getLogger("xiaozhi.admin.firmware")
router = APIRouter(prefix="/admin/firmware", tags=["Admin Firmware Marketplace"])


@router.get("/products", response_class=HTMLResponse)
async def admin_products_page(request: Request, status: Optional[str] = None):
    user = require_admin(request)
    repo = get_marketplace_repo()
    products = repo.get_all_products_admin(status=status)
    return render(request, "admin/firmware_products.html", {
        "user": user,
        "page": "admin_firmware_products",
        "products": products,
        "current_filter": status or "all",
    })


@router.post("/products/{product_id}/delete")
async def admin_delete_product_action(request: Request, product_id: str):
    user = require_admin(request)
    service = get_product_service()
    try:
        res = service.delete_product(product_id, user)
        action_text = "diarsipkan (karena memiliki histori transaksi)" if res.get("action") == "archived" else "berhasil dihapus"
        return redirect_with_message("/admin/firmware/products", f"Produk {action_text}.")
    except Exception as exc:
        return redirect_with_message("/admin/firmware/products", f"Gagal menghapus produk: {str(exc)}")


@router.get("/approvals", response_class=HTMLResponse)
async def admin_approvals_page(request: Request):
    user = require_admin(request)
    service = get_approval_service()
    approvals = service.get_pending_approvals()
    return render(request, "admin/firmware_approvals.html", {
        "user": user,
        "page": "admin_firmware_approvals",
        "approvals": approvals,
    })


@router.post("/approvals/{approval_id}/approve")
async def admin_approve_action(request: Request, approval_id: str):
    user = require_admin(request)
    service = get_approval_service()
    try:
        success = service.approve_product(approval_id, int(user["id"]))
        if success:
            return redirect_with_message("/admin/firmware/approvals", "Produk firmware berhasil disetujui dan dipublikasikan.")
        return redirect_with_message("/admin/firmware/approvals", "Gagal: Approval tidak ditemukan atau sudah diproses.")
    except Exception as exc:
        return redirect_with_message("/admin/firmware/approvals", f"Terjadi kesalahan: {str(exc)}")


@router.post("/approvals/{approval_id}/reject")
async def admin_reject_action(request: Request, approval_id: str, reason: str = Form(...)):
    user = require_admin(request)
    service = get_approval_service()
    try:
        success = service.reject_product(approval_id, int(user["id"]), reason)
        if success:
            return redirect_with_message("/admin/firmware/approvals", "Produk firmware ditolak dan alasan dikirim ke seller.")
        return redirect_with_message("/admin/firmware/approvals", "Gagal: Approval tidak ditemukan atau sudah diproses.")
    except Exception as exc:
        return redirect_with_message("/admin/firmware/approvals", f"Gagal menolak: {str(exc)}")


@router.get("/finance", response_class=HTMLResponse)
async def admin_finance_dashboard(request: Request):
    user = require_admin(request)
    service = get_wallet_service()
    finance_data = service.get_admin_finance_data()
    return render(request, "admin/firmware_finance.html", {
        "user": user,
        "page": "admin_firmware_finance",
        "finance": finance_data,
    })
