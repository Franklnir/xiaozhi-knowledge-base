import unittest
from fastapi.testclient import TestClient
from fastapi import FastAPI
import shutil
import tempfile
from pathlib import Path

from xiaozhi.routers.chat import router as chat_router
from xiaozhi.routers.admin_firmware_ui import router as admin_firmware_router
from xiaozhi.services.preset_approval_service import (
    is_user_authorized,
    get_user_status,
    generate_claim_code,
    redeem_claim_code,
    revoke_access,
    list_granted_users,
    _load_data,
    _save_data,
)

test_app = FastAPI()
test_app.include_router(chat_router)
test_app.include_router(admin_firmware_router)


class TestMultiPresetClaims(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(test_app)
        self.admin_user = {"id": 1, "username": "admin", "role": "admin"}
        self.user_test = {"id": 999, "username": "multi_tester", "role": "user"}

    def test_01_multi_preset_claim_preservation(self):
        """Test that claiming S3 then claiming C3 preserves both presets simultaneously."""
        uname = self.user_test["username"]

        # 1. Clean up user if existed
        revoke_access(uname, self.admin_user, "all")
        self.assertFalse(is_user_authorized(self.user_test, "esp32s3_cam"))
        self.assertFalse(is_user_authorized(self.user_test, "esp32c3ytmusikchronchi"))

        # 2. Generate claim code for S3
        code_s3 = generate_claim_code(preset_id="esp32s3_cam", note="Test S3", admin_user=self.admin_user)
        self.assertIsNotNone(code_s3)

        # 3. Redeem S3 code
        res_s3 = redeem_claim_code(code_s3["code"], self.user_test)
        self.assertTrue(res_s3["success"])
        self.assertIn("request_id", res_s3)
        self.assertIn("esp32s3_cam", res_s3["active_presets"])

        # Check authorization: S3 is True, C3 is False
        self.assertTrue(is_user_authorized(self.user_test, "esp32s3_cam"))
        self.assertFalse(is_user_authorized(self.user_test, "esp32c3ytmusikchronchi"))
        self.assertEqual(get_user_status(self.user_test, "esp32s3_cam")["status"], "APPROVED")
        self.assertEqual(get_user_status(self.user_test, "esp32c3ytmusikchronchi")["status"], "NONE")

        # 4. Generate claim code for C3
        code_c3 = generate_claim_code(preset_id="esp32c3ytmusikchronchi", note="Test C3", admin_user=self.admin_user)
        self.assertIsNotNone(code_c3)

        # 5. Redeem C3 code (THIS USED TO OVERWRITE AND WIPE S3!)
        res_c3 = redeem_claim_code(code_c3["code"], self.user_test)
        self.assertTrue(res_c3["success"])
        self.assertIn("request_id", res_c3)
        self.assertIn("esp32c3ytmusikchronchi", res_c3["active_presets"])
        self.assertIn("esp32s3_cam", res_c3["active_presets"])

        # 6. CRUCIAL VERIFICATION: BOTH PRESETS MUST BE AUTHORIZED!
        self.assertTrue(is_user_authorized(self.user_test, "esp32s3_cam"), "S3 must NOT disappear after claiming C3!")
        self.assertTrue(is_user_authorized(self.user_test, "esp32c3ytmusikchronchi"), "C3 must be active!")
        self.assertEqual(get_user_status(self.user_test, "esp32s3_cam")["status"], "APPROVED")
        self.assertEqual(get_user_status(self.user_test, "esp32c3ytmusikchronchi")["status"], "APPROVED")

        # 7. Check list_granted_users structure
        granted_all = list_granted_users()
        self.assertIn(uname, granted_all)
        user_info = granted_all[uname]
        self.assertEqual(user_info["presets_count"], 2)
        preset_ids = [p["preset_id"] for p in user_info["presets_list"]]
        self.assertIn("esp32s3_cam", preset_ids)
        self.assertIn("esp32c3ytmusikchronchi", preset_ids)

        # 8. Revoking only S3 must leave C3 active!
        revoke_access(uname, self.admin_user, "esp32s3_cam")
        self.assertFalse(is_user_authorized(self.user_test, "esp32s3_cam"), "S3 should be revoked")
        self.assertTrue(is_user_authorized(self.user_test, "esp32c3ytmusikchronchi"), "C3 must remain active!")

        # 9. Revoking C3 clears user
        revoke_access(uname, self.admin_user, "esp32c3ytmusikchronchi")
        self.assertFalse(is_user_authorized(self.user_test, "esp32c3ytmusikchronchi"))

    def test_02_request_id_and_error_handling(self):
        """Test that invalid and already-used claims return request_id and proper error codes."""
        # Invalid code
        res_invalid = redeem_claim_code("XZ-INVALID-1234-5678", self.user_test)
        self.assertFalse(res_invalid["success"])
        self.assertEqual(res_invalid["error_code"], "INVALID_CODE")
        self.assertTrue(res_invalid["request_id"].startswith("req_"))

        # Already used code
        code_s3 = generate_claim_code(preset_id="esp32s3_cam", note="Test Double Claim", admin_user=self.admin_user)
        redeem_claim_code(code_s3["code"], self.user_test)
        
        # Try claiming again
        user_2 = {"id": 1000, "username": "other_user", "role": "user"}
        res_used = redeem_claim_code(code_s3["code"], user_2)
        self.assertFalse(res_used["success"])
        self.assertEqual(res_used["error_code"], "CODE_ALREADY_USED")
        self.assertTrue(res_used["request_id"].startswith("req_"))

        # Clean up
        revoke_access(self.user_test["username"], self.admin_user, "all")


if __name__ == "__main__":
    unittest.main()
