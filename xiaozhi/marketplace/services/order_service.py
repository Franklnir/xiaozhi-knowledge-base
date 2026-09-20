import time
import uuid
from typing import Dict, Any, Tuple

from xiaozhi.config import MARKETPLACE_ADMIN_FEE_FLAT, MARKETPLACE_ADMIN_FEE_PERCENT
from xiaozhi.marketplace.repository import MarketplaceRepository
from xiaozhi.marketplace.payments import get_payment_provider


class OrderService:
    def __init__(self, repo: MarketplaceRepository):
        self.repo = repo

    async def create_checkout_order(self, buyer: Dict[str, Any], product_id: str) -> Tuple[Dict[str, Any], str]:
        buyer_id = int(buyer["id"])

        # 1. Fetch product & latest version
        product = self.repo.get_product_by_id(product_id)
        if not product:
            raise ValueError("Produk firmware tidak ditemukan.")
        if product["status"] != "PUBLISHED":
            raise ValueError("Produk ini belum dipublikasikan atau sedang ditinjau.")
        if product["seller_id"] == buyer_id:
            raise ValueError("Anda tidak dapat membeli produk firmware milik Anda sendiri.")

        # 2. Check duplicate ownership
        if self.repo.check_existing_entitlement(buyer_id, product_id):
            raise ValueError("Anda sudah memiliki produk firmware ini. Silakan unduh di menu 'Pembelian Saya'.")

        latest_version = self.repo.get_latest_version(product_id)
        if not latest_version or not latest_version.get("sha256"):
            raise ValueError("Binary firmware untuk produk ini belum siap.")

        # 3. Compute transparent fees
        subtotal = int(product["price_amount"])
        platform_fee = int(MARKETPLACE_ADMIN_FEE_FLAT + int(subtotal * MARKETPLACE_ADMIN_FEE_PERCENT))
        platform_fee = min(platform_fee, subtotal)
        seller_net = subtotal - platform_fee
        buyer_total = subtotal

        # 4. Generate order number
        order_number = f"FW-{int(time.time())}-{uuid.uuid4().hex[:6].upper()}"

        # 5. Create Order record in DB
        order = self.repo.create_order(
            buyer_id=buyer_id,
            seller_id=product["seller_id"],
            product_id=product_id,
            version_id=str(latest_version["id"]),
            order_number=order_number,
            subtotal_amount=subtotal,
            platform_fee_amount=platform_fee,
            buyer_total_amount=buyer_total,
            seller_net_amount=seller_net,
            product_title_snapshot=product["title"],
            seller_name_snapshot=product.get("seller_username", "Seller"),
            version_label_snapshot=latest_version["version_label"],
            firmware_sha256_snapshot=latest_version["sha256"],
        )

        # 6. Call payment provider to get checkout URL
        provider = get_payment_provider()
        checkout_res = await provider.create_checkout(order)

        # 7. Record payment transaction intent
        with self.repo._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO payment_transactions (
                        order_id, provider, provider_transaction_id, checkout_url, amount, currency, status
                    ) VALUES (%s, %s, %s, %s, %s, 'IDR', 'INITIATED');
                """, (
                    order["id"], checkout_res.provider_name, checkout_res.provider_reference,
                    checkout_res.checkout_url, buyer_total
                ))
                conn.commit()

        return order, checkout_res.checkout_url
