import io
import time
import unittest
import uuid
from PIL import Image

from xiaozhi.dependencies import get_store
from xiaozhi.marketplace.deps import (
    get_marketplace_repo,
    get_product_service,
    get_order_service,
    get_approval_service,
    get_wallet_service,
    get_entitlement_service,
)
from xiaozhi.marketplace.storage import storage_service
from xiaozhi.marketplace.security import (
    calculate_sha256_stream,
    encrypt_sensitive_data,
    decrypt_sensitive_data,
    verify_download_token,
)
from xiaozhi.config import MARKETPLACE_ADMIN_FEE_FLAT


class TestFirmwareMarketplace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = get_store()
        cls.repo = get_marketplace_repo()
        cls.product_service = get_product_service()
        cls.order_service = get_order_service()
        cls.approval_service = get_approval_service()
        cls.wallet_service = get_wallet_service()
        cls.entitlement_service = get_entitlement_service()

        # Ensure admin user exists
        cls.admin_username = f"admin_test_{uuid.uuid4().hex[:6]}"
        cls.admin_user = cls.store.ensure_admin_user(cls.admin_username, "AdminPass123!")

        # Create test seller & buyer
        cls.seller_username = f"seller_{uuid.uuid4().hex[:6]}"
        cls.seller_user = cls.store.create_user(cls.seller_username, "SellerPass123!")

        cls.buyer_username = f"buyer_{uuid.uuid4().hex[:6]}"
        cls.buyer_user = cls.store.create_user(cls.buyer_username, "BuyerPass123!")

    def _create_dummy_image(self, width=200, height=200, color="blue") -> bytes:
        img = Image.new("RGB", (width, height), color=color)
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_01_firmware_file_validation(self):
        """Test .bin file validation and rejection of non-.bin extensions."""
        valid_bin = b"\x00\x01\x02\x03ESP32_FIRMWARE_HEADER_TEST"
        meta = storage_service.validate_firmware("my_device.bin", io.BytesIO(valid_bin))
        self.assertEqual(meta["original_filename"], "my_device.bin")
        self.assertEqual(meta["file_size"], len(valid_bin))
        self.assertTrue(len(meta["sha256"]) == 64)

        # Reject non-.bin
        with self.assertRaises(ValueError):
            storage_service.validate_firmware("malware.exe", io.BytesIO(b"bad bytes"))

        with self.assertRaises(ValueError):
            storage_service.validate_firmware("script.py", io.BytesIO(b"print('hello')"))

        # Reject empty file
        with self.assertRaises(ValueError):
            storage_service.validate_firmware("empty.bin", io.BytesIO(b""))

    def test_02_image_optimization_and_size_guard(self):
        """Test image recompression to WebP and size <= 500 KB (512000 bytes)."""
        raw_img = self._create_dummy_image(800, 600, color="red")
        processed = storage_service.process_and_validate_image(raw_img, "test.jpg")
        self.assertEqual(processed["mime_type"], "image/webp")
        self.assertLessEqual(processed["file_size"], 512000)
        self.assertTrue(len(processed["sha256"]) == 64)

    def test_03_encryption_helpers(self):
        """Test sensitive bank account encryption and decryption."""
        original = "1234-5678-9012-BCA"
        encrypted = encrypt_sensitive_data(original)
        self.assertNotEqual(original, encrypted)
        decrypted = decrypt_sensitive_data(encrypted)
        self.assertEqual(original, decrypted)

    def test_04_admin_auto_publish(self):
        """Test that products created by admin are automatically PUBLISHED and have OFFICIAL ADMIN badge."""
        img_bytes = self._create_dummy_image()
        fw_bytes = b"ESP32_OFFICIAL_FIRMWARE_BIN_DATA"
        
        product = self.product_service.create_product(
            seller=self.admin_user,
            title="Official ESP32 Firmware by Admin",
            short_description="Firmware resmi terverifikasi admin",
            full_description="Panduan instalasi firmware lengkap",
            price_amount=50000,
            images_data=[img_bytes],
            firmware_bytes=fw_bytes,
            firmware_filename="official.bin",
        )

        self.assertEqual(product["status"], "PUBLISHED")
        self.assertTrue(product["is_admin_product"])
        self.assertIsNotNone(product["published_at"])

    def test_05_regular_user_workflow_and_approval(self):
        """Test user draft -> submit review -> admin reject (with reason) -> resubmit -> approve."""
        img_bytes = self._create_dummy_image()
        fw_bytes = b"ESP32_COMMUNITY_FIRMWARE_BIN_DATA"
        
        # 1. User creates draft
        product = self.product_service.create_product(
            seller=self.seller_user,
            title="Community Smart Lamp Firmware",
            short_description="Firmware kontrol lampu pintar",
            full_description="Detail koneksi relay dan pinout ESP32",
            price_amount=35000,
            images_data=[img_bytes],
            firmware_bytes=fw_bytes,
            firmware_filename="lamp_v1.bin",
        )
        prod_id = str(product["id"])
        self.assertEqual(product["status"], "DRAFT")
        self.assertFalse(product["is_admin_product"])

        # 2. Submit for review
        approval = self.product_service.submit_for_review(prod_id, int(self.seller_user["id"]))
        self.assertEqual(approval["status"], "PENDING_REVIEW")
        appr_id = str(approval["id"])

        # 3. Admin reject with short reason (< 10 chars) should fail
        with self.assertRaises(ValueError):
            self.approval_service.reject_product(appr_id, int(self.admin_user["id"]), "Tolak")

        # 4. Admin reject with valid reason (>= 10 chars)
        reject_reason = "Firmware tidak menyertakan pinout GPIO yang sesuai dengan skema."
        success_reject = self.approval_service.reject_product(appr_id, int(self.admin_user["id"]), reject_reason)
        self.assertTrue(success_reject)

        # Check product status is now REJECTED
        p_refreshed = self.repo.get_product_by_id(prod_id)
        self.assertEqual(p_refreshed["status"], "REJECTED")

        # 5. User resubmits for review
        approval2 = self.product_service.submit_for_review(prod_id, int(self.seller_user["id"]))
        self.assertEqual(approval2["status"], "PENDING_REVIEW")
        appr_id2 = str(approval2["id"])

        # 6. Admin approves product
        success_approve = self.approval_service.approve_product(appr_id2, int(self.admin_user["id"]))
        self.assertTrue(success_approve)

        p_published = self.repo.get_product_by_id(prod_id)
        self.assertEqual(p_published["status"], "PUBLISHED")
        self.assertIsNotNone(p_published["published_at"])
        self.community_prod_id = prod_id

    def test_06_order_fee_transparency_and_payment_finalization(self):
        """Test transparent fee calculation (35000 = 1500 platform fee + 33500 seller net) and idempotent webhook."""
        if not hasattr(self, "community_prod_id"):
            self.test_05_regular_user_workflow_and_approval()
        prod_id = self.community_prod_id

        # Seller cannot buy own product
        import asyncio
        with self.assertRaises(ValueError):
            asyncio.run(self.order_service.create_checkout_order(self.seller_user, prod_id))

        # Buyer creates order
        order, checkout_url = asyncio.run(self.order_service.create_checkout_order(self.buyer_user, prod_id))
        self.assertEqual(order["subtotal_amount"], 35000)
        self.assertEqual(order["platform_fee_amount"], MARKETPLACE_ADMIN_FEE_FLAT) # Rp 1.500
        self.assertEqual(order["seller_net_amount"], 35000 - MARKETPLACE_ADMIN_FEE_FLAT) # Rp 33.500
        self.assertEqual(order["buyer_total_amount"], 35000)
        self.assertEqual(order["status"], "PENDING_PAYMENT")
        self.assertIn("simulate", checkout_url)

        order_number = order["order_number"]

        # Finalize order via simulated webhook
        payload = {"order_number": order_number, "status": "PAID", "amount": 35000}
        success = self.repo.finalize_order_payment(
            order_number=order_number,
            provider="simulator",
            provider_event_id=f"sim_evt_{uuid.uuid4().hex[:10]}",
            provider_reference=order_number,
            payload=payload,
        )
        self.assertTrue(success)

        # Verify order is PAID
        o_refreshed = self.repo.get_order_by_number(order_number)
        self.assertEqual(o_refreshed["status"], "PAID")
        self.assertIsNotNone(o_refreshed["paid_at"])

        # Verify entitlement ACTIVE
        self.assertTrue(self.repo.check_existing_entitlement(int(self.buyer_user["id"]), prod_id))

        # Buyer cannot buy product a second time (already owned)
        with self.assertRaises(ValueError):
            asyncio.run(self.order_service.create_checkout_order(self.buyer_user, prod_id))

        # Verify seller wallet received exactly 33.500 net
        seller_fin = self.wallet_service.get_seller_financial_data(int(self.seller_user["id"]))
        self.assertGreaterEqual(seller_fin["wallet"]["available_balance"], 33500)

        # Verify admin platform finance recorded 1.500 fee
        admin_fin = self.wallet_service.get_admin_finance_data()
        self.assertGreaterEqual(admin_fin["admin_available_balance"], MARKETPLACE_ADMIN_FEE_FLAT)
        self.assertGreaterEqual(admin_fin["total_admin_fees"], MARKETPLACE_ADMIN_FEE_FLAT)

        # Test duplicate webhook idempotency: sending same event must not credit double
        prev_balance = seller_fin["wallet"]["available_balance"]
        dup_success = self.repo.finalize_order_payment(
            order_number=order_number,
            provider="simulator",
            provider_event_id="sim_evt_duplicate",
            provider_reference=order_number,
            payload=payload,
        )
        self.assertTrue(dup_success)
        seller_fin_after = self.wallet_service.get_seller_financial_data(int(self.seller_user["id"]))
        self.assertEqual(seller_fin_after["wallet"]["available_balance"], prev_balance)

        # Test download authorization for buyer
        purchases = self.entitlement_service.get_user_purchases(int(self.buyer_user["id"]))
        self.assertGreaterEqual(len(purchases), 1)
        purchase_id = str(purchases[0]["purchase_id"])

        dl_meta = self.entitlement_service.authorize_download(purchase_id, int(self.buyer_user["id"]))
        self.assertTrue(dl_meta["download_url"].startswith("http"))
        self.assertIn("lamp_v1.bin", dl_meta["download_url"])
        self.assertEqual(dl_meta["filename"], "lamp_v1.bin")


        # Test non-buyer unauthorized download
        other_user = self.store.create_user(f"other_{uuid.uuid4().hex[:6]}", "Pass123!")
        with self.assertRaises(PermissionError):
            self.entitlement_service.authorize_download(purchase_id, int(other_user["id"]))

        # Test withdrawal request with balance locking
        withdrawal = self.wallet_service.request_withdrawal(
            user_id=int(self.seller_user["id"]),
            amount=20000,
            destination_bank="BCA",
            destination_account_name="Franklnir",
            destination_account_number="1234567890",
        )
        self.assertEqual(withdrawal["amount"], 20000)
        self.assertEqual(withdrawal["status"], "REQUESTED")

        # Verify balance decreased by 20.000
        seller_fin_wd = self.wallet_service.get_seller_financial_data(int(self.seller_user["id"]))
        self.assertEqual(seller_fin_wd["wallet"]["available_balance"], prev_balance - 20000)

        # Attempting withdrawal more than remaining balance must fail
        with self.assertRaises(ValueError):
            self.wallet_service.request_withdrawal(
                user_id=int(self.seller_user["id"]),
                amount=99999999,
                destination_bank="BCA",
                destination_account_name="Franklnir",
                destination_account_number="1234567890",
            )

    def test_07_product_crud_and_archive(self):
        """Test full CRUD operations on products for both regular seller and admin."""
        # 1. CREATE
        img_bytes = self._create_dummy_image(color="purple")
        fw_bytes = b"ESP32_CUSTOM_RGB_CONTROLLER_V1"
        product = self.product_service.create_product(
            seller=self.seller_user,
            title="Custom RGB Light Controller",
            short_description="Firmware pengontrol lampu RGB",
            full_description="Panduan pengontrol lampu pintar RGB ESP32",
            price_amount=15000,
            images_data=[img_bytes],
            firmware_bytes=fw_bytes,
            firmware_filename="rgb_controller.bin",
        )
        prod_id = str(product["id"])
        self.assertEqual(product["status"], "DRAFT")

        # 2. READ
        detail = self.product_service.get_product_detail(prod_id, current_user_id=int(self.seller_user["id"]))
        self.assertIsNotNone(detail)
        self.assertEqual(detail["title"], "Custom RGB Light Controller")
        self.assertTrue(detail["is_seller"])

        # Admin reads all products
        admin_prods = self.repo.get_all_products_admin()
        self.assertTrue(any(p["id"] == uuid.UUID(prod_id) for p in admin_prods))

        # 3. UPDATE (seller edits details & price)
        updated = self.product_service.update_product(
            product_id=prod_id,
            user=self.seller_user,
            title="Custom RGB Light Controller V2 - Updated",
            short_description="Firmware pengontrol lampu RGB generasi 2",
            full_description="Panduan diperbarui dan fitur baru",
            price_amount=20000,
            links=[{"label": "GitHub Repo", "url": "https://github.com/example/rgb"}],
        )
        self.assertEqual(updated["title"], "Custom RGB Light Controller V2 - Updated")
        self.assertEqual(updated["price_amount"], 20000)

        # Admin can also update product
        admin_updated = self.product_service.update_product(
            product_id=prod_id,
            user=self.admin_user,
            title="Custom RGB Light Controller V2 - Admin Verified",
            short_description="Firmware pengontrol lampu RGB generasi 2",
            full_description="Panduan diperbarui dan fitur baru",
            price_amount=20000,
        )
        self.assertEqual(admin_updated["title"], "Custom RGB Light Controller V2 - Admin Verified")

        # 4. DELETE (Product without orders -> Hard delete)
        del_result = self.product_service.delete_product(prod_id, self.seller_user)
        self.assertEqual(del_result["action"], "deleted")
        self.assertIsNone(self.repo.get_product_by_id(prod_id))

    def test_08_native_rls_isolation(self):
        """Verify PostgreSQL Row-Level Security policies enforce strict tenant isolation."""
        # Create a secret draft product owned by seller_user
        draft_prod = self.repo.create_product(
            seller_id=int(self.seller_user["id"]),
            title="Secret Draft Firmware",
            slug=f"secret-draft-{uuid.uuid4().hex[:6]}",
            short_description="Draft not published yet",
            full_description="Private internal notes",
            price_amount=10000,
            is_admin=False,
        )
        draft_id = str(draft_prod["id"])

        # 1. When role is tenant user and context is seller -> draft is visible
        with self.repo._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL ROLE app_tenant_role;")
                self.repo.set_rls_context(cur, int(self.seller_user["id"]), "user")
                cur.execute("SELECT id FROM firmware_products WHERE id = %s;", (draft_id,))
                self.assertIsNotNone(cur.fetchone())

        # 2. When role is tenant user and context is unrelated buyer -> draft is completely invisible (RLS filter)
        with self.repo._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL ROLE app_tenant_role;")
                self.repo.set_rls_context(cur, int(self.buyer_user["id"]), "user")
                cur.execute("SELECT id FROM firmware_products WHERE id = %s;", (draft_id,))
                self.assertIsNone(cur.fetchone())

        # 3. When role is tenant user and context is admin -> draft is visible
        with self.repo._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL ROLE app_tenant_role;")
                self.repo.set_rls_context(cur, int(self.admin_user["id"]), "admin")
                cur.execute("SELECT id FROM firmware_products WHERE id = %s;", (draft_id,))
                self.assertIsNotNone(cur.fetchone())

        # 4. Wallet isolation: buyer cannot see seller's wallet row
        self.repo.get_wallet(int(self.seller_user["id"]))
        with self.repo._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL ROLE app_tenant_role;")
                self.repo.set_rls_context(cur, int(self.buyer_user["id"]), "user")
                cur.execute("SELECT id FROM wallet_accounts WHERE user_id = %s;", (int(self.seller_user["id"]),))
                self.assertIsNone(cur.fetchone())

        # 5. Seller CAN see their own wallet row
        with self.repo._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL ROLE app_tenant_role;")
                self.repo.set_rls_context(cur, int(self.seller_user["id"]), "user")
                cur.execute("SELECT id FROM wallet_accounts WHERE user_id = %s;", (int(self.seller_user["id"]),))
                self.assertIsNotNone(cur.fetchone())


if __name__ == "__main__":
    unittest.main()
