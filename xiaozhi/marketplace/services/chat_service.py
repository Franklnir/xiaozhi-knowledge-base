import json
import logging
from typing import List, Dict, Any, Optional
from xiaozhi.marketplace.repository import MarketplaceRepository
from xiaozhi.marketplace.security import rate_limiter
from xiaozhi.marketplace.storage import storage_service

logger = logging.getLogger("xiaozhi.marketplace.chat")


class MarketplaceChatService:
    def __init__(self, repo: MarketplaceRepository):
        self.repo = repo

    def get_or_start_chat(self, product_id: str, seller_id: int, buyer_id: int) -> Dict[str, Any]:
        if int(seller_id) == int(buyer_id):
            raise ValueError("Anda tidak dapat memulai obrolan pada produk milik Anda sendiri.")
        return self.repo.get_or_create_conversation(product_id, int(seller_id), int(buyer_id))

    def get_conversation(self, conversation_id: str, user_id: int) -> Dict[str, Any]:
        conv = self.repo.get_conversation_by_id(conversation_id)
        if not conv:
            raise LookupError("Percakapan tidak ditemukan.")
        if int(conv["seller_id"]) != int(user_id) and int(conv["buyer_id"]) != int(user_id):
            raise PermissionError("Akses percakapan ditolak (privat).")
        return conv

    def get_user_conversations(self, user_id: int) -> List[Dict[str, Any]]:
        return self.repo.get_user_conversations(int(user_id))

    def get_total_unread_count(self, user_id: int) -> int:
        return self.repo.get_total_unread_chat_count(int(user_id))

    def get_messages(self, conversation_id: str, user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        # Verify user is a member of this conversation
        self.get_conversation(conversation_id, user_id)
        # Auto-mark incoming messages as read
        self.repo.mark_conversation_as_read(conversation_id, int(user_id))
        return self.repo.get_messages(conversation_id, limit=limit)

    def send_message(self, conversation_id: str, sender_id: int, body: str) -> Dict[str, Any]:
        # Verify user is a member of this conversation
        self.get_conversation(conversation_id, sender_id)

        rate_key = f"chat_send_{sender_id}"
        if not rate_limiter.is_allowed(rate_key, max_requests=40, window_seconds=60):
            raise PermissionError("Batas pengiriman pesan terlampaui (maksimal 40 pesan per menit).")

        clean_body = body.strip()
        if not clean_body:
            raise ValueError("Pesan tidak boleh kosong.")
        if len(clean_body) > 3000:
            raise ValueError("Pesan maksimal 3000 karakter.")

        return self.repo.send_message(conversation_id, int(sender_id), clean_body)

    def get_seller_shareable_products(self, seller_id: int) -> List[Dict[str, Any]]:
        """Returns active published products owned by this seller that can be shared in chat."""
        products = self.repo.get_seller_products(int(seller_id), status="PUBLISHED")
        result = []
        for p in products:
            img_url = None
            if p.get("primary_image_key"):
                img_url = storage_service.get_image_url(p["primary_image_key"])
            result.append({
                "id": str(p["id"]),
                "slug": p.get("slug") or str(p["id"]),
                "title": p["title"],
                "price_amount": int(p["price_amount"]),
                "image_url": img_url,
                "has_firmware": bool(p.get("has_firmware", True)),
                "has_stl": bool(p.get("has_stl", False)),
            })
        return result

    def send_product_card(
        self,
        conversation_id: str,
        sender_id: int,
        product_id: str,
        note: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Allows a seller to share a product card into the chat.
        STRICT VALIDATION: Only products owned by the seller sending the card are allowed.
        """
        conv = self.get_conversation(conversation_id, sender_id)
        if int(conv["seller_id"]) != int(sender_id):
            raise PermissionError("Hanya penjual dalam percakapan ini yang dapat membagikan kartu produk.")

        # Verify product ownership
        product = self.repo.get_product_by_id(product_id)
        if not product:
            raise LookupError("Produk yang dipilih tidak ditemukan.")
        if int(product["seller_id"]) != int(sender_id):
            raise PermissionError("Anda hanya dapat membagikan produk milik Anda sendiri.")
        if product.get("status") not in ("PUBLISHED", "APPROVED"):
            raise ValueError("Hanya produk yang sudah berstatus terbit yang dapat dibagikan.")

        # Resolve primary image
        img_url = None
        if product.get("images") and len(product["images"]) > 0:
            img_url = product["images"][0].get("url")
        elif product.get("primary_image_key"):
            img_url = storage_service.get_image_url(product["primary_image_key"])

        card_payload = {
            "type": "product_card",
            "product_id": str(product["id"]),
            "slug": product.get("slug") or str(product["id"]),
            "title": product["title"],
            "price": int(product["price_amount"]),
            "image_url": img_url,
            "has_firmware": bool(product.get("has_firmware", True)),
            "has_stl": bool(product.get("has_stl", False)),
            "note": (note or "").strip()[:500],
        }

        body = json.dumps(card_payload)
        return self.send_message(conversation_id, sender_id, body)
