import json
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from xiaozhi.dependencies import init_dependencies
from xiaozhi.routers import admin_firmware_ui as admin_fw_router_mod
from xiaozhi.routers import chat as chat_router_mod
from xiaozhi.services import announcement_service, preset_approval_service

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _build_test_app() -> FastAPI:
    app = FastAPI()
    static_dir = PROJECT_ROOT / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))
    init_dependencies(templates, store=None)
    app.include_router(chat_router_mod.router)
    app.include_router(admin_fw_router_mod.router)
    return app


def _seed_sample_presets(access_file: Path) -> None:
    payload: Dict[str, Any] = {
        "presets": {
            "board_alpha": {
                "id": "board_alpha",
                "title": "Board Alpha ESP32-S3",
                "name": "Board Alpha ESP32-S3",
                "chip": "ESP32-S3",
                "offset": "0x0",
                "description": "Firmware Alpha untuk display ST7789",
                "enc_rel_path": "secret/alpha.enc",
                "storage_bucket": "private-bucket",
                "storage_key": "firmware_presets/board_alpha_v001.enc",
                "filename": "alpha_v001.bin",
                "size_bytes": 2097152,
                "active_version": "v001",
                "versions": [
                    {
                        "version": "v001",
                        "filename": "alpha_v001.bin",
                        "storage_bucket": "private-bucket",
                        "storage_key": "firmware_presets/board_alpha_v001.enc",
                        "size_bytes": 2097152,
                        "sha256": "a" * 64,
                        "changelog": "Rilis perdana Alpha stabil",
                        "uploaded_at": "2026-11-01T10:00:00+00:00",
                        "uploaded_by": "admin",
                    }
                ],
            },
            "board_beta": {
                "id": "board_beta",
                "title": "Board Beta ESP32-C3 Audio",
                "name": "Board Beta ESP32-C3 Audio",
                "chip": "ESP32-C3",
                "offset": "0x10000",
                "description": "Firmware Beta untuk ESP32-C3 Mini",
                "filename": "beta_v002.bin",
                "size_bytes": 1572864,
                "active_version": "v002",
                "versions": [
                    {
                        "version": "v001",
                        "filename": "beta_v001.bin",
                        "storage_bucket": "private-bucket",
                        "storage_key": "firmware_presets/board_beta_v001.enc",
                        "size_bytes": 1500000,
                        "sha256": "b" * 64,
                        "changelog": "Rilis awal Beta audio C3",
                        "uploaded_at": "2026-10-15T08:00:00+00:00",
                        "uploaded_by": "admin",
                    },
                    {
                        "version": "v002",
                        "filename": "beta_v002.bin",
                        "storage_bucket": "private-bucket",
                        "storage_key": "firmware_presets/board_beta_v002.enc",
                        "size_bytes": 1572864,
                        "sha256": "c" * 64,
                        "changelog": "Perbaikan panjang buffer musik & dukungan WakeWord penuh tanpa putus.",
                        "uploaded_at": "2026-11-10T12:30:00+00:00",
                        "uploaded_by": "admin",
                    },
                ],
            },
        },
        "granted_users": {
            "beta_owner": {
                "username": "beta_owner",
                "presets": {
                    "board_beta": {
                        "preset_id": "board_beta",
                        "granted_by": "admin",
                        "granted_at": "2026-11-05T09:00:00+00:00",
                    }
                },
            }
        },
        "claim_codes": [
            {
                "code": "XZ-PRESET-SECRET-1234",
                "preset_id": "board_alpha",
                "used": False,
            }
        ],
    }
    access_file.parent.mkdir(parents=True, exist_ok=True)
    access_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch):
    access_file = tmp_path / "preset_flasher_access.json"
    _seed_sample_presets(access_file)

    monkeypatch.setattr(preset_approval_service, "_get_data_file", lambda: access_file)
    monkeypatch.setattr(preset_approval_service, "ensure_default_presets_in_bucket", lambda: None)
    monkeypatch.setattr(announcement_service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(announcement_service, "ANNOUNCEMENT_FILE", tmp_path / "announcement.json")

    return {"tmp_path": tmp_path, "access_file": access_file}


def test_get_user_authorized_presets_and_sanitization(isolated_env):
    admin = {"id": 1, "username": "admin", "role": "admin"}
    owner = {"id": 42, "username": "BETA_OWNER", "role": "user"}
    stranger = {"id": 99, "username": "stranger", "role": "user"}

    assert preset_approval_service.get_user_authorized_presets(None, ["board_alpha", "board_beta"]) == []
    assert preset_approval_service.get_user_authorized_presets(admin, ["board_alpha", "board_beta"]) == ["board_alpha", "board_beta"]
    assert preset_approval_service.get_user_authorized_presets(owner, ["board_alpha", "board_beta"]) == ["board_beta"]
    assert preset_approval_service.get_user_authorized_presets(stranger, ["board_alpha", "board_beta"]) == []

    safe = preset_approval_service.sanitize_preset_for_client(preset_approval_service.get_preset("board_alpha"))
    raw_dump = json.dumps(safe)
    assert "private-bucket" not in raw_dump
    assert "secret/alpha.enc" not in raw_dump
    assert "firmware_presets/" not in raw_dump


def test_changelog_menu_removed_safely(isolated_env, monkeypatch):
    app = _build_test_app()
    monkeypatch.setattr(chat_router_mod, "get_current_user", lambda req: None)
    client = TestClient(app)

    for path in ("/changelog", "/info-update", "/api/v1/changelog"):
        resp = client.get(path)
        assert resp.status_code == 404


def test_web_flasher_smart_default_unauth_and_version_box(isolated_env, monkeypatch):
    app = _build_test_app()
    client = TestClient(app)

    # 1. Guest / expired session: sees #boxPresetLoginRequired visible, #boxPresetLocked hidden
    monkeypatch.setattr(chat_router_mod, "get_current_user", lambda req: None)
    resp_guest = client.get("/web-flasher")
    assert resp_guest.status_code == 200
    html_guest = resp_guest.text
    assert 'id="verDetailChangelog"' in html_guest
    assert 'id="verDetailDate"' in html_guest
    assert 'id="versionStatusPill"' in html_guest
    assert 'id="olderVersionAlert"' in html_guest
    assert 'id="boxPresetLoginRequired" style=""' in html_guest
    assert 'id="boxPresetLocked" style="display:none;"' in html_guest
    assert 'Perlu Masuk Akun' in html_guest
    assert 'href="/changelog"' not in html_guest
    assert 'id="verUpdateActionBadge"' not in html_guest

    # 2. Logged-in user who owns board_beta (not board_alpha): smart default selects board_beta
    owner = {"id": 42, "username": "beta_owner", "role": "user"}
    monkeypatch.setattr(chat_router_mod, "get_current_user", lambda req: owner)
    resp_owner = client.get("/web-flasher")
    assert resp_owner.status_code == 200
    html_owner = resp_owner.text
    assert 'let currentPresetId = "board_beta"' in html_owner
    assert 'id="boxPresetUnlocked" style=""' in html_owner
    assert 'id="boxPresetLoginRequired" style="display:none;"' in html_owner
    assert 'Lisensi Aktif' in html_owner

    # 3. Query params ?preset=board_beta&version=v001 select v001 cleanly
    resp_ver = client.get("/web-flasher?preset=board_beta&version=v001")
    assert resp_ver.status_code == 200
    html_ver = resp_ver.text
    assert 'const initialSelectedVersion = "v001"' in html_ver
    assert 'Versi: v001' in html_ver


def test_admin_approvals_page_clean_without_changelog_tab(isolated_env, monkeypatch):
    app = _build_test_app()
    admin = {"id": 1, "username": "admin_user", "role": "admin"}
    client = TestClient(app)

    class _FakeApprovals:
        def get_pending_approvals(self):
            return []

    monkeypatch.setattr(admin_fw_router_mod, "require_admin", lambda req: admin)
    monkeypatch.setattr(admin_fw_router_mod, "get_approval_service", lambda: _FakeApprovals())

    page_resp = client.get("/admin/firmware/approvals")
    assert page_resp.status_code == 200
    assert "tabBtnServerChangelog" not in page_resp.text
    assert "/admin/firmware/server-changelog" not in page_resp.text

