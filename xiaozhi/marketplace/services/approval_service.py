from typing import List, Dict, Any
from xiaozhi.marketplace.repository import MarketplaceRepository


class ApprovalService:
    def __init__(self, repo: MarketplaceRepository):
        self.repo = repo

    def get_pending_approvals(self) -> List[Dict[str, Any]]:
        return self.repo.get_pending_approvals()

    def approve_product(self, approval_id: str, admin_user_id: int) -> bool:
        return self.repo.admin_approve_product(approval_id, admin_user_id)

    def reject_product(self, approval_id: str, admin_user_id: int, reason: str) -> bool:
        if not reason or len(reason.strip()) < 10:
            raise ValueError("Alasan penolakan wajib diisi (minimal 10 karakter) agar seller dapat memperbaikinya.")
        return self.repo.admin_reject_product(approval_id, admin_user_id, reason.strip())
