from typing import Optional
from xiaozhi.config import (
    ALLOW_SIMULATOR_PAYMENTS,
    MARKETPLACE_PAYMENT_PROVIDER,
    PAYMENTS_ENABLED,
)
from xiaozhi.marketplace.payments.base import PaymentProvider, CheckoutResult, VerifiedWebhookEvent
from xiaozhi.marketplace.payments.xendit import XenditPaymentProvider
from xiaozhi.marketplace.payments.midtrans import MidtransPaymentProvider
from xiaozhi.marketplace.payments.simulator import SimulatorPaymentProvider


def get_payment_provider(provider_name: Optional[str] = None) -> PaymentProvider:
    if not PAYMENTS_ENABLED:
        raise RuntimeError("Pembayaran marketplace sedang dinonaktifkan.")
    name = (provider_name or MARKETPLACE_PAYMENT_PROVIDER or "disabled").strip().lower()
    if name == "xendit":
        return XenditPaymentProvider()
    elif name == "midtrans":
        return MidtransPaymentProvider()
    elif name == "simulator" and ALLOW_SIMULATOR_PAYMENTS:
        return SimulatorPaymentProvider()
    raise RuntimeError("Provider pembayaran tidak aktif atau tidak didukung.")


__all__ = [
    "PaymentProvider",
    "CheckoutResult",
    "VerifiedWebhookEvent",
    "XenditPaymentProvider",
    "MidtransPaymentProvider",
    "SimulatorPaymentProvider",
    "get_payment_provider",
]
