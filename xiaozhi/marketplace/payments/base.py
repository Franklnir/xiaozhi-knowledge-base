from dataclasses import dataclass
from typing import Dict, Any, Optional, Protocol


@dataclass
class CheckoutResult:
    checkout_url: str
    provider_reference: str
    order_number: str
    provider_name: str


@dataclass
class VerifiedWebhookEvent:
    event_id: str
    order_number: str
    amount: int
    status: str  # PAID, EXPIRED, CANCELLED, REFUNDED
    provider: str
    raw_payload: Dict[str, Any]


class PaymentProvider(Protocol):
    async def create_checkout(self, order: Dict[str, Any]) -> CheckoutResult:
        ...

    async def verify_webhook(self, headers: Dict[str, str], raw_body: bytes) -> VerifiedWebhookEvent:
        ...

    async def get_payment_status(self, provider_reference: str) -> str:
        ...
