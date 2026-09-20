import hashlib
import json
import uuid
from typing import Dict, Any

from xiaozhi.marketplace.payments.base import CheckoutResult, VerifiedWebhookEvent


class SimulatorPaymentProvider:
    """
    Sandbox Simulator payment provider for local development & testing.
    Allows zero-cost testing of the entire purchase, webhook, and entitlement workflow.
    """
    def __init__(self):
        self.provider_name = "simulator"

    async def create_checkout(self, order: Dict[str, Any]) -> CheckoutResult:
        order_number = order["order_number"]
        return CheckoutResult(
            checkout_url=f"/firmware/checkout/simulate?order_number={order_number}",
            provider_reference=f"sim_{order_number}",
            order_number=order_number,
            provider_name="simulator",
        )

    async def verify_webhook(self, headers: Dict[str, str], raw_body: bytes) -> VerifiedWebhookEvent:
        payload = json.loads(raw_body.decode("utf-8"))
        order_number = str(payload.get("order_number", ""))
        status = str(payload.get("status", "PAID")).upper()
        amount = int(payload.get("amount", 0))
        event_id = str(payload.get("event_id", f"sim_evt_{uuid.uuid4().hex[:12]}"))

        return VerifiedWebhookEvent(
            event_id=event_id,
            order_number=order_number,
            amount=amount,
            status=status,
            provider="simulator",
            raw_payload=payload,
        )

    async def get_payment_status(self, provider_reference: str) -> str:
        return "PAID"
