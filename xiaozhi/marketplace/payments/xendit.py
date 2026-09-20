import base64
import hmac
import json
import logging
from typing import Dict, Any
import requests

from xiaozhi.config import XENDIT_SECRET_KEY, XENDIT_WEBHOOK_TOKEN
from xiaozhi.marketplace.payments.base import CheckoutResult, VerifiedWebhookEvent

logger = logging.getLogger("xiaozhi.marketplace.xendit")


class XenditPaymentProvider:
    """
    Xendit payment adapter supporting hosted checkout (Invoices) and authenticated webhooks.
    """
    def __init__(self):
        self.secret_key = XENDIT_SECRET_KEY
        self.webhook_token = XENDIT_WEBHOOK_TOKEN
        self.base_url = "https://api.xendit.co/v2/invoices"

    async def create_checkout(self, order: Dict[str, Any]) -> CheckoutResult:
        order_number = order["order_number"]
        amount = int(order["buyer_total_amount"])
        title = order.get("product_title_snapshot", "Firmware Binary")

        payload = {
            "external_id": order_number,
            "amount": amount,
            "description": f"Firmware: {title}",
            "invoice_duration": 86400,
            "currency": "IDR",
        }

        # Basic Auth with secret_key as username, blank password
        auth_header = "Basic " + base64.b64encode(f"{self.secret_key}:".encode()).decode()
        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(self.base_url, json=payload, headers=headers, timeout=15)
            data = resp.json()
            if resp.status_code not in (200, 201):
                error_msg = data.get("message", f"Xendit error {resp.status_code}")
                raise ValueError(f"Gagal membuat invoice Xendit: {error_msg}")

            invoice_url = data.get("invoice_url")
            invoice_id = data.get("id")
            return CheckoutResult(
                checkout_url=invoice_url,
                provider_reference=str(invoice_id),
                order_number=order_number,
                provider_name="xendit",
            )
        except Exception as exc:
            logger.error("Error creating Xendit invoice: %s", exc)
            raise ValueError(f"Koneksi ke Xendit gagal: {str(exc)}")

    async def verify_webhook(self, headers: Dict[str, str], raw_body: bytes) -> VerifiedWebhookEvent:
        provided_token = headers.get("x-callback-token") or headers.get("X-CALLBACK-TOKEN", "")
        if not self.webhook_token or not hmac.compare_digest(provided_token, self.webhook_token):
            raise PermissionError("Token callback Xendit tidak valid (x-callback-token mismatch).")

        payload = json.loads(raw_body.decode("utf-8"))
        event_id = str(payload.get("id", ""))
        order_number = str(payload.get("external_id", ""))
        status_raw = str(payload.get("status", "")).upper()
        amount = int(payload.get("amount", 0))

        status = "PENDING_PAYMENT"
        if status_raw in ("PAID", "SETTLED"):
            status = "PAID"
        elif status_raw in ("EXPIRED",):
            status = "EXPIRED"

        return VerifiedWebhookEvent(
            event_id=event_id,
            order_number=order_number,
            amount=amount,
            status=status,
            provider="xendit",
            raw_payload=payload,
        )

    async def get_payment_status(self, provider_reference: str) -> str:
        auth_header = "Basic " + base64.b64encode(f"{self.secret_key}:".encode()).decode()
        headers = {"Authorization": auth_header}
        resp = requests.get(f"{self.base_url}/{provider_reference}", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            status_raw = str(data.get("status", "")).upper()
            if status_raw in ("PAID", "SETTLED"):
                return "PAID"
            elif status_raw in ("EXPIRED",):
                return "EXPIRED"
        return "PENDING_PAYMENT"
