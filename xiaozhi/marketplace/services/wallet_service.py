import time
import uuid
from typing import Dict, Any

from xiaozhi.config import MINIMUM_WITHDRAWAL, WITHDRAWAL_FEE
from xiaozhi.marketplace.repository import MarketplaceRepository
from xiaozhi.marketplace.security import encrypt_sensitive_data


class WalletService:
    def __init__(self, repo: MarketplaceRepository):
        self.repo = repo

    def get_seller_financial_data(self, seller_id: int) -> Dict[str, Any]:
        wallet = self.repo.get_wallet(seller_id)
        summary = self.repo.get_seller_sales_summary(seller_id)
        ledger = self.repo.get_wallet_ledger(seller_id, limit=50)
        withdrawals = self.repo.get_seller_withdrawals(seller_id)
        orders = self.repo.get_seller_orders_list(seller_id, limit=50)

        return {
            "wallet": wallet,
            "summary": summary,
            "ledger": ledger,
            "withdrawals": withdrawals,
            "orders": orders,
            "minimum_withdrawal": MINIMUM_WITHDRAWAL,
            "withdrawal_fee": WITHDRAWAL_FEE,
        }

    def request_withdrawal(
        self,
        user_id: int,
        amount: int,
        destination_bank: str,
        destination_account_name: str,
        destination_account_number: str,
    ) -> Dict[str, Any]:
        if amount < MINIMUM_WITHDRAWAL:
            raise ValueError(f"Jumlah penarikan minimal adalah Rp {MINIMUM_WITHDRAWAL:,}.")

        fee = WITHDRAWAL_FEE
        net_amount = amount - fee
        if net_amount <= 0:
            raise ValueError("Jumlah penarikan setelah dipotong biaya admin harus lebih dari Rp 0.")

        bank = destination_bank.strip()
        name = destination_account_name.strip()
        num = destination_account_number.strip()

        if not bank or not name or not num:
            raise ValueError("Nama bank, nama pemilik rekening, dan nomor rekening wajib diisi.")

        encrypted_num = encrypt_sensitive_data(num)
        idempotency_key = f"wd_{user_id}_{int(time.time())}_{uuid.uuid4().hex[:6]}"

        return self.repo.request_withdrawal(
            user_id=user_id,
            amount=amount,
            fee=fee,
            net_amount=net_amount,
            destination_bank=bank,
            destination_account_name=name,
            destination_account_encrypted=encrypted_num,
            idempotency_key=idempotency_key,
        )

    def get_admin_finance_data(self) -> Dict[str, Any]:
        return self.repo.get_admin_platform_finance()
