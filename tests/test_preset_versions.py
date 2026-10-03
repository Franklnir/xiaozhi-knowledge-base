import hashlib
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaozhi.routers.chat import router as chat_router
from xiaozhi.routers.admin_firmware_ui import router as admin_firmware_router
from xiaozhi.services.preset_approval_service import (
    get_preset_version_binary,
    get_decrypted_preset_binary,
    sanitize_preset_for_client,
    get_preset,
    get_user_status,
    save_or_update_preset,
    set_active_preset_version,
    grant_access_direct,
    revoke_access,
)

test_app = FastAPI()
test_app.include_router(chat_router)
test_app.include_router(admin_firmware_router)


class TestPresetVersions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(test_app)
        cls.admin_user = {"id": 1, "username": "admin", "role": "admin"}
        cls.buyer_user = {"id": 777, "username": "buyer_version_tester", "role": "user"}

    def test_01_fetch_v001_with_sha256_integrity(self):
        """Test fetching v001 explicitly and verifying integrity check."""
        raw_bytes, ver_meta = get_preset_version_binary("esp32s3_cam", version="v001")
        self.assertGreater(len(raw_bytes), 1000000)
        self.assertEqual(ver_meta.get("version"), "v001")
        self.assertEqual(hashlib.sha256(raw_bytes).hexdigest(), ver_meta.get("sha256"))

    def test_02_strict_rejection_of_nonexistent_version(self):
        """Ensure requesting non-existent version raises ValueError and never silent fallbacks."""
        with self.assertRaises(ValueError) as ctx:
            get_preset_version_binary("esp32s3_cam", version="v999")
        self.assertIn("tidak ditemukan", str(ctx.exception).lower())

    def test_03_rejection_of_malformed_version_strings(self):
        """Path traversal or malformed version parameters must be strictly rejected."""
        malicious_inputs = ["../../etc/passwd", "v001; rm -rf", "v001\x00malicious", "v001/../"]
        for bad_ver in malicious_inputs:
            with self.assertRaises(ValueError):
                get_preset_version_binary("esp32s3_cam", version=bad_ver)

    def test_04_sanitize_preset_for_client(self):
        """Metadata sent to client must be sanitized from internal storage keys."""
        preset = get_preset("esp32s3_cam")
        self.assertIsNotNone(preset)
        safe = sanitize_preset_for_client(preset)
        self.assertNotIn("enc_rel_path", safe)
        for v in safe.get("versions", []):
            self.assertNotIn("storage_bucket", v)
            self.assertNotIn("storage_key", v)
            self.assertIn("version", v)
            self.assertIn("is_active", v)
            self.assertIn("sha256_short", v)

    def test_05_admin_set_active_version_and_rollback(self):
        """Admin can change active version back and forth (rollback)."""
        preset = get_preset("esp32s3_cam")
        self.assertIsNotNone(preset)

        # Set to v001 explicitly
        updated = set_active_preset_version("esp32s3_cam", "v001", admin_user=self.admin_user)
        self.assertEqual(updated.get("active_version"), "v001")

        # Non-existent version should fail
        with self.assertRaises(ValueError):
            set_active_preset_version("esp32s3_cam", "v9999", admin_user=self.admin_user)

    def test_06_fastapi_stream_security_and_version_filtering(self):
        """Test API endpoint authorization, headers, and version query parameter."""
        # 1. Unauthenticated request should be 403 (commercial preset)
        resp_unauth = self.client.get("/api/v1/flasher/preset/esp32s3_cam/stream?version=v001")
        self.assertEqual(resp_unauth.status_code, 403)

        # 2. Grant access to buyer
        grant_access_direct(self.buyer_user["username"], self.admin_user, "esp32s3_cam")

        # Mock authenticated user
        from xiaozhi.routers import chat

        orig_get_user = chat.get_current_user
        chat.get_current_user = lambda request: self.buyer_user

        try:
            # Authorized stream for v001
            resp_ok = self.client.get("/api/v1/flasher/preset/esp32s3_cam/stream?version=v001")
            self.assertEqual(resp_ok.status_code, 200)
            self.assertEqual(resp_ok.headers.get("X-Firmware-Version"), "v001")
            self.assertEqual(resp_ok.headers.get("X-Firmware-Offset"), "0x0")
            self.assertIn("no-store", resp_ok.headers.get("Cache-Control", ""))
            self.assertIn("nosniff", resp_ok.headers.get("X-Content-Type-Options", ""))

            # Requesting non-existent version must return 404
            resp_404 = self.client.get("/api/v1/flasher/preset/esp32s3_cam/stream?version=v999")
            self.assertEqual(resp_404.status_code, 404)

            # Requesting invalid characters must return 400
            resp_400 = self.client.get("/api/v1/flasher/preset/esp32s3_cam/stream?version=../../bad")
            self.assertEqual(resp_400.status_code, 400)
        finally:
            chat.get_current_user = orig_get_user
            revoke_access(self.buyer_user["username"], self.admin_user, "esp32s3_cam")


if __name__ == "__main__":
    unittest.main()
