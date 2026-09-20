from typing import Optional
from xiaozhi.config import MARKETPLACE_PAYMENT_PROVIDER
from xiaozhi.marketplace.payments.base import PaymentProvider, CheckoutResult, VerifiedWebhookEvent
from xiaozhi.marketplace.payments.xendit import XenditPaymentProvider
from xiaozhi.marketplace.payments.midtrans import MidtransPaymentProvider
from xiaozhi.marketplace.payments.simulator import SimulatorPaymentProvider


def get_payment_provider(provider_name: Optional[str] = None) -> PaymentProvider:
    name = (provider_name or MARKETPLACE_PAYMENT_PROVIDER or "simulator").strip().lower()
    if name == "xendit":
        return XenditPaymentProvider()
    elif name == "midtrans":
        return MidtransPaymentProvider()
    else:
        return SimulatorPaymentProvider()


__all__ = [
    "PaymentProvider",
    "CheckoutResult",
    "VerifiedWebhookEvent",
    "XenditPaymentProvider",
    "MidtransPaymentProvider",
    "SimulatorPaymentProvider",
    "get_payment_provider",
]
