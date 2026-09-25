from xiaozhi.services.preset_approval_service import (
    list_all_requests as list_preset_requests,
    list_granted_users as list_preset_granted_users,
    approve_request as approve_preset_request,
    reject_request as reject_preset_request,
    grant_access_direct as grant_preset_direct,
    revoke_access as revoke_preset_access,
    list_presets,
    save_or_update_preset,
    generate_claim_code,
    list_claim_codes,
    delete_claim_code,
)
import logging
from fastapi import APIRouter, Request, Form, Query, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse

from xiaozhi.dependencies import render, require_admin, redirect_with_message, get_store
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
    preset_requests = list_preset_requests()
    preset_granted = list_preset_granted_users()
    presets = list_presets()
    claim_codes = list_claim_codes()
    return render(request, "admin/firmware_approvals.html", {
        "user": user,
        "page": "admin_firmware_approvals",
        "approvals": approvals,
        "preset_requests": preset_requests,
        "preset_granted": preset_granted,
        "presets": presets,
        "claim_codes": claim_codes,
    })


@router.post("/preset-approvals/{request_id}/approve")
async def admin_approve_preset_action(request: Request, request_id: str):
    user = require_admin(request)
    success = approve_preset_request(request_id, user)
    if success:
        return redirect_with_message("/admin/firmware/approvals", "Izin preset berhasil disetujui. Akses pengguna aktif.")
    return redirect_with_message("/admin/firmware/approvals", "Permintaan izin preset tidak ditemukan.")


@router.post("/preset-approvals/{request_id}/reject")
async def admin_reject_preset_action(request: Request, request_id: str, reason: str = Form("Ditolak oleh admin")):
    user = require_admin(request)
    success = reject_preset_request(request_id, user, reason)
    if success:
        return redirect_with_message("/admin/firmware/approvals", "Permintaan izin preset berhasil ditolak.")
    return redirect_with_message("/admin/firmware/approvals", "Permintaan izin preset tidak ditemukan.")


@router.post("/preset-approvals/grant")
async def admin_grant_preset_action(request: Request, username: str = Form(...), preset_id: str = Form("esp32s3_cam")):
    user = require_admin(request)
    store = get_store()
    raw_username = (username or "").strip()

    # Validasi keberadaan akun di database agar nama asal-asalan tidak bisa diberi akses
    try:
        target_user = store.get_user_by_username(raw_username)
    except Exception:
        target_user = None

    if not target_user:
        return redirect_with_message(
            "/admin/firmware/approvals",
            f"Gagal: Pengguna '{raw_username}' tidak terdaftar di sistem. Akses hanya dapat diberikan ke akun resmi yang ada."
        )

    exact_username = target_user.get("username", raw_username)
    success = grant_preset_direct(exact_username, user, preset_id)
    if success:
        return redirect_with_message("/admin/firmware/approvals", f"Akses preset berhasil diberikan langsung ke '{exact_username}'.")
    return redirect_with_message("/admin/firmware/approvals", "Gagal memproses pemberian akses.")


@router.post("/preset-approvals/revoke")
async def admin_revoke_preset_action(request: Request, username: str = Form(...), preset_id: str = Form("esp32s3_cam")):
    user = require_admin(request)
    success = revoke_preset_access(username, user, preset_id)
    if success:
        return redirect_with_message("/admin/firmware/approvals", f"Akses preset '{username}' berhasil dicabut.")
    return redirect_with_message("/admin/firmware/approvals", "Pengguna tidak ditemukan.")


@router.post("/presets/save")
async def admin_save_preset_action(
    request: Request,
    preset_id: str = Form(""),
    title: str = Form(""),
    chip: str = Form("ESP32-S3"),
    offset: str = Form("0x0"),
    description: str = Form(""),
    changelog: str = Form(""),
    firmware_file: Optional[UploadFile] = File(None),
):
    user = require_admin(request)
    file_bytes = None
    filename = None
    if firmware_file and firmware_file.filename:
        file_bytes = await firmware_file.read()
        filename = firmware_file.filename

    if not title and not preset_id:
        return redirect_with_message("/admin/firmware/approvals", "Gagal: Judul preset atau ID wajib diisi.")

    try:
        updated_preset = save_or_update_preset(
            preset_id=preset_id or title,
            title=title or preset_id,
            chip=chip,
            offset=offset,
            description=description,
            file_bytes=file_bytes,
            filename=filename,
            admin_user=user,
            changelog=changelog,
        )
        ver = updated_preset.get("active_version", "v001")
        msg = f"Preset '{updated_preset.get('title')}' berhasil disimpan. Versi aktif: {ver}."
        if file_bytes:
            msg += " File .bin berhasil dienkripsi dan diunggah ke Bucket Storage."
        return redirect_with_message("/admin/firmware/approvals", msg)
    except Exception as exc:
        logger.exception("Error saving preset firmware")
        return redirect_with_message("/admin/firmware/approvals", f"Gagal menyimpan preset: {str(exc)}")


@router.post("/claim-codes/generate")
async def admin_generate_claim_code_action(
    request: Request,
    preset_id: str = Form("esp32s3_cam"),
    note: str = Form(""),
):
    user = require_admin(request)
    try:
        code_rec = generate_claim_code(preset_id=preset_id, note=note, admin_user=user)
        return redirect_with_message(
            "/admin/firmware/approvals",
            f"Kode lisensi sekali pakai '{code_rec['code']}' berhasil dibuat! Berikan kode ini ke pembeli.",
        )
    except Exception as exc:
        return redirect_with_message("/admin/firmware/approvals", f"Gagal membuat kode: {str(exc)}")


@router.post("/claim-codes/{code_id}/delete")
async def admin_delete_claim_code_action(request: Request, code_id: str):
    user = require_admin(request)
    success = delete_claim_code(code_id, user)
    if success:
        return redirect_with_message("/admin/firmware/approvals", "Kode lisensi berhasil dihapus.")
    return redirect_with_message("/admin/firmware/approvals", "Kode tidak ditemukan atau sudah pernah diklaim.")


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
