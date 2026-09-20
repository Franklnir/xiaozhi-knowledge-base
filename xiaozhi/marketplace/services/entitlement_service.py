from typing import List, Dict, Any

from xiaozhi.marketplace.repository import MarketplaceRepository
from xiaozhi.marketplace.storage import storage_service
from xiaozhi.marketplace.security import rate_limiter


class EntitlementService:
    def __init__(self, repo: MarketplaceRepository):
        self.repo = repo

    def get_user_purchases(self, buyer_id: int) -> List[Dict[str, Any]]:
        purchases = self.repo.get_buyer_purchases(buyer_id)
        for p in purchases:
            p["image_url"] = storage_service.get_image_url(p.get("primary_image_key", ""))
        return purchases

    def authorize_download(self, purchase_id: str, buyer_id: int, asset_type: str = "bin") -> Dict[str, Any]:
        # Rate limit check: max 10 download requests per minute per user
        rate_key = f"dl_auth_{buyer_id}"
        if not rate_limiter.is_allowed(rate_key, max_requests=10, window_seconds=60):
            raise PermissionError("Batas unduhan terlampaui (maksimal 10x per menit). Silakan tunggu sebentar.")

        clean_asset_type = str(asset_type).lower().strip()
        if clean_asset_type not in ("bin", "stl"):
            clean_asset_type = "bin"

        # Database lookup with ownership & entitlement verification
        entitlement = self.repo.get_entitlement_for_download(purchase_id, buyer_id, asset_type=clean_asset_type)
        if not entitlement:
            file_desc = "file model 3D .stl" if clean_asset_type == "stl" else "file binary firmware .bin"
            raise PermissionError(f"Akses unduhan ditolak. {file_desc.capitalize()} tidak tersedia atau lisensi tidak valid.")

        storage_key = entitlement["storage_key"]
        default_name = "model.stl" if clean_asset_type == "stl" else "firmware.bin"
        filename = entitlement["original_filename"] or default_name

        # Generate short-lived signed URL (10 minutes = 600 seconds)
        if clean_asset_type == "stl":
            download_url = storage_service.get_stl_download_url(
                purchase_id=purchase_id,
                storage_key=storage_key,
                filename=filename,
                expires_in_seconds=600,
            )
            action_name = "STL_DOWNLOADED"
        else:
            download_url = storage_service.get_firmware_download_url(
                purchase_id=purchase_id,
                storage_key=storage_key,
                filename=filename,
                expires_in_seconds=600,
            )
            action_name = "FIRMWARE_DOWNLOADED"

        # Audit download event
        with self.repo._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO audit_logs (
                        actor_user_id, actor_role, action, entity_type, entity_id, reason
                    ) VALUES (%s, 'buyer', %s, 'purchase_entitlement', %s, %s);
                """, (buyer_id, action_name, purchase_id, f"Download authorized for {filename}"))
                conn.commit()

        return {
            "download_url": download_url,
            "filename": filename,
            "sha256": entitlement["sha256"],
            "file_size": entitlement["file_size"],
            "expires_in_seconds": 600,
            "asset_type": clean_asset_type,
        }
