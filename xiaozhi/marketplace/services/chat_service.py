from typing import List, Dict, Any
from xiaozhi.marketplace.repository import MarketplaceRepository
from xiaozhi.marketplace.security import rate_limiter


class MarketplaceChatService:
    def __init__(self, repo: MarketplaceRepository):
        self.repo = repo

    def get_or_start_chat(self, product_id: str, seller_id: int, buyer_id: int) -> Dict[str, Any]:
        return self.repo.get_or_create_conversation(product_id, seller_id, buyer_id)

    def get_messages(self, conversation_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        return self.repo.get_messages(conversation_id, limit=limit)

    def send_message(self, conversation_id: str, sender_id: int, body: str) -> Dict[str, Any]:
        rate_key = f"chat_send_{sender_id}"
        if not rate_limiter.is_allowed(rate_key, max_requests=30, window_seconds=60):
            raise PermissionError("Batas pengiriman pesan terlampaui (maksimal 30 pesan per menit).")

        clean_body = body.strip()
        if not clean_body:
            raise ValueError("Pesan tidak boleh kosong.")
        if len(clean_body) > 2000:
            raise ValueError("Pesan maksimal 2000 karakter.")

        return self.repo.send_message(conversation_id, sender_id, clean_body)
