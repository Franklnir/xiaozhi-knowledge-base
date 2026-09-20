import logging
from fastapi import APIRouter, Request, Form, Query, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse

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
async def admin_finance_dashboard(
    request: Request,
    tab: str = Query("transactions"),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1),
):
    user = require_admin(request)
    service = get_wallet_service()
    repo = get_marketplace_repo()

    finance_data = service.get_admin_finance_data()
    limit = 30
    offset = (max(1, page) - 1) * limit
    orders, total_orders = repo.get_admin_orders(limit=limit, offset=offset, status=status, search=q)
    withdrawals = repo.get_admin_withdrawals(limit=50)
    pending_wd_count = sum(1 for w in withdrawals if w.get("status") in ("REQUESTED", "PROCESSING"))

    total_pages = max(1, (total_orders + limit - 1) // limit)

    return render(request, "admin/firmware_finance.html", {
        "user": user,
        "page": "admin_firmware_finance",
        "finance": finance_data,
        "orders": orders,
        "total_orders": total_orders,
        "withdrawals": withdrawals,
        "pending_withdrawals_count": pending_wd_count,
        "current_tab": tab if tab in ("transactions", "withdrawals") else "transactions",
        "status_filter": status or "ALL",
        "search_query": q or "",
        "page_num": page,
        "total_pages": total_pages,
    })


@router.get("/api/orders/{order_id_or_number}")
async def admin_get_order_detail_api(request: Request, order_id_or_number: str):
    user = require_admin(request)
    repo = get_marketplace_repo()
    detail = repo.get_admin_order_detail(order_id_or_number)
    if not detail:
        raise HTTPException(status_code=404, detail="Detail transaksi pesanan tidak ditemukan.")

    # Convert timestamps and UUIDs for JSON serialization
    safe_detail = {}
    for k, v in detail.items():
        if hasattr(v, "isoformat"):
            safe_detail[k] = v.isoformat()
        elif hasattr(v, "hex"):
            safe_detail[k] = str(v)
        elif isinstance(v, list):
            safe_list = []
            for item in v:
                if isinstance(item, dict):
                    safe_sub = {
                        sk: (sv.isoformat() if hasattr(sv, "isoformat") else (str(sv) if hasattr(sv, "hex") else sv))
                        for sk, sv in item.items()
                    }
                    safe_list.append(safe_sub)
                else:
                    safe_list.append(item)
            safe_detail[k] = safe_list
        else:
            safe_detail[k] = v

    return JSONResponse({"success": True, "order": safe_detail})


@router.post("/withdrawals/{withdrawal_id}/approve")
async def admin_approve_withdrawal_action(
    request: Request,
    withdrawal_id: str,
    admin_notes: Optional[str] = Form(None),
):
    user = require_admin(request)
    repo = get_marketplace_repo()
    try:
        repo.admin_process_withdrawal(
            withdrawal_id=withdrawal_id,
            action="approve",
            admin_user_id=int(user["id"]),
            admin_notes=admin_notes or "Transfer berhasil diproses oleh admin.",
        )
        return redirect_with_message("/admin/firmware/finance?tab=withdrawals", "Permintaan penarikan dana berhasil disetujui & ditandai selesai.")
    except Exception as exc:
        return redirect_with_message("/admin/firmware/finance?tab=withdrawals", f"Gagal menyetujui penarikan: {str(exc)}")


@router.post("/withdrawals/{withdrawal_id}/reject")
async def admin_reject_withdrawal_action(
    request: Request,
    withdrawal_id: str,
    reason: str = Form(...),
):
    user = require_admin(request)
    repo = get_marketplace_repo()
    try:
        repo.admin_process_withdrawal(
            withdrawal_id=withdrawal_id,
            action="reject",
            admin_user_id=int(user["id"]),
            admin_notes=reason,
        )
        return redirect_with_message("/admin/firmware/finance?tab=withdrawals", "Permintaan penarikan dana ditolak dan saldo telah dikembalikan secara aman ke dompet penjual.")
    except Exception as exc:
        return redirect_with_message("/admin/firmware/finance?tab=withdrawals", f"Gagal menolak penarikan: {str(exc)}")
