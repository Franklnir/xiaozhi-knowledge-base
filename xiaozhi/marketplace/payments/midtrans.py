import base64
import hashlib
import hmac
import json
import logging
from typing import Dict, Any
import requests

from xiaozhi.config import MIDTRANS_SERVER_KEY, MIDTRANS_IS_PRODUCTION
from xiaozhi.marketplace.payments.base import CheckoutResult, VerifiedWebhookEvent

logger = logging.getLogger("xiaozhi.marketplace.midtrans")


class MidtransPaymentProvider:
    """
    Midtrans payment adapter supporting Snap hosted checkout and SHA512 signature verification.
    """
    def __init__(self):
        self.server_key = MIDTRANS_SERVER_KEY
        self.is_production = MIDTRANS_IS_PRODUCTION
        if self.is_production:
            self.snap_url = "https://app.midtrans.com/snap/v1/transactions"
            self.api_url = "https://api.midtrans.com/v2"
        else:
            self.snap_url = "https://app.sandbox.midtrans.com/snap/v1/transactions"
            self.api_url = "https://api.sandbox.midtrans.com/v2"

    async def create_checkout(self, order: Dict[str, Any]) -> CheckoutResult:
        order_number = order["order_number"]
        amount = int(order["buyer_total_amount"])
        title = order.get("product_title_snapshot", "Firmware Binary")

        payload = {
            "transaction_details": {
                "order_id": order_number,
                "gross_amount": amount,
            },
            "item_details": [
                {
                    "id": str(order["product_id"]),
                    "price": amount,
                    "quantity": 1,
                    "name": title[:50],
                }
            ],
        }

        auth_header = "Basic " + base64.b64encode(f"{self.server_key}:".encode()).decode()
        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            resp = requests.post(self.snap_url, json=payload, headers=headers, timeout=15)
            data = resp.json()
            if resp.status_code not in (200, 201):
                error_msg = data.get("error_messages", [f"Midtrans error {resp.status_code}"])[0]
                raise ValueError(f"Gagal membuat transaksi Snap Midtrans: {error_msg}")

            redirect_url = data.get("redirect_url")
            token = data.get("token")
            return CheckoutResult(
                checkout_url=redirect_url,
                provider_reference=str(token),
                order_number=order_number,
                provider_name="midtrans",
            )
        except Exception as exc:
            logger.error("Error creating Midtrans transaction: %s", exc)
            raise ValueError(f"Koneksi ke Midtrans gagal: {str(exc)}")

    async def verify_webhook(self, headers: Dict[str, str], raw_body: bytes) -> VerifiedWebhookEvent:
        payload = json.loads(raw_body.decode("utf-8"))
        order_id = str(payload.get("order_id", ""))
        status_code = str(payload.get("status_code", ""))
        gross_amount = str(payload.get("gross_amount", ""))
        provided_sig = str(payload.get("signature_key", ""))

        # Expected = SHA512(order_id + status_code + gross_amount + ServerKey)
        sign_str = f"{order_id}{status_code}{gross_amount}{self.server_key}"
        expected_sig = hashlib.sha512(sign_str.encode("utf-8")).hexdigest()

        if not hmac.compare_digest(provided_sig, expected_sig):
            raise PermissionError("Signature key Midtrans tidak cocok.")

        transaction_status = payload.get("transaction_status", "")
        fraud_status = payload.get("fraud_status", "")
        transaction_id = str(payload.get("transaction_id", order_id))

        status = "PENDING_PAYMENT"
        if transaction_status in ("capture", "settlement"):
            if fraud_status == "challenge":
                status = "PENDING_PAYMENT"
            else:
                status = "PAID"
        elif transaction_status in ("cancel", "deny"):
            status = "CANCELLED"
        elif transaction_status in ("expire",):
            status = "EXPIRED"

        amount_val = int(float(gross_amount)) if gross_amount else 0

        return VerifiedWebhookEvent(
            event_id=transaction_id,
            order_number=order_id,
            amount=amount_val,
            status=status,
            provider="midtrans",
            raw_payload=payload,
        )

    async def get_payment_status(self, provider_reference: str) -> str:
        auth_header = "Basic " + base64.b64encode(f"{self.server_key}:".encode()).decode()
        headers = {"Authorization": auth_header}
        resp = requests.get(f"{self.api_url}/{provider_reference}/status", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            tx_status = data.get("transaction_status", "")
            if tx_status in ("capture", "settlement"):
                return "PAID"
            elif tx_status in ("expire",):
                return "EXPIRED"
        return "PENDING_PAYMENT"
