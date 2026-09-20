from xiaozhi.dependencies import get_store
from xiaozhi.marketplace.repository import MarketplaceRepository
from xiaozhi.marketplace.services.product_service import ProductService
from xiaozhi.marketplace.services.order_service import OrderService
from xiaozhi.marketplace.services.approval_service import ApprovalService
from xiaozhi.marketplace.services.wallet_service import WalletService
from xiaozhi.marketplace.services.entitlement_service import EntitlementService
from xiaozhi.marketplace.services.chat_service import MarketplaceChatService

_repo = None
_product_service = None
_order_service = None
_approval_service = None
_wallet_service = None
_entitlement_service = None
_chat_service = None


def get_marketplace_repo() -> MarketplaceRepository:
    global _repo
    if _repo is None:
        store = get_store()
        _repo = MarketplaceRepository(store)
    return _repo


def get_product_service() -> ProductService:
    global _product_service
    if _product_service is None:
        _product_service = ProductService(get_marketplace_repo())
    return _product_service


def get_order_service() -> OrderService:
    global _order_service
    if _order_service is None:
        _order_service = OrderService(get_marketplace_repo())
    return _order_service


def get_approval_service() -> ApprovalService:
    global _approval_service
    if _approval_service is None:
        _approval_service = ApprovalService(get_marketplace_repo())
    return _approval_service


def get_wallet_service() -> WalletService:
    global _wallet_service
    if _wallet_service is None:
        _wallet_service = WalletService(get_marketplace_repo())
    return _wallet_service


def get_entitlement_service() -> EntitlementService:
    global _entitlement_service
    if _entitlement_service is None:
        _entitlement_service = EntitlementService(get_marketplace_repo())
    return _entitlement_service


def get_chat_service() -> MarketplaceChatService:
    global _chat_service
    if _chat_service is None:
        _chat_service = MarketplaceChatService(get_marketplace_repo())
    return _chat_service
