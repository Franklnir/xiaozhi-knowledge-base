import json
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from xiaozhi.dependencies import init_dependencies, make_csrf_token
from xiaozhi.routers import admin_firmware_ui as admin_fw_router_mod
from xiaozhi.routers import chat as chat_router_mod
from xiaozhi.services import announcement_service, changelog_service, preset_approval_service

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
                        "update_type": "reconnect",
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

    monkeypatch.setattr(changelog_service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(changelog_service, "CHANGELOG_FILE", tmp_path / "server_changelog.json")
    monkeypatch.setattr(announcement_service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(announcement_service, "ANNOUNCEMENT_FILE", tmp_path / "announcement.json")

    monkeypatch.setattr(
        preset_approval_service.storage_service,
        "upload_encrypted_preset",
        lambda pid, ver, data: ("test-bucket", f"firmware_presets/{pid}_{ver}.enc"),
    )

    return {
        "tmp_path": tmp_path,
        "access_file": access_file,
        "changelog_file": tmp_path / "server_changelog.json",
    }


def test_changelog_service_crud_and_seed(isolated_env):
    seeded = changelog_service.list_server_updates()
    assert len(seeded) == 1
    assert seeded[0]["id"] == "srv_init_mcp_sync"
    assert seeded[0]["update_type"] == "server_reconnect"

    with pytest.raises(ValueError):
        changelog_service.add_server_update(title="", description="Detail valid")
    with pytest.raises(ValueError):
        changelog_service.add_server_update(title="Judul valid", description="   ")

    added = changelog_service.add_server_update(
        title="Perbaikan Tool Musik & Smart Home",
        description="Sekarang mendukung filter 7 hari & koreksi nama ruang otomatis.",
        update_type="invalid_value",
        target="Musik & Relay",
        admin_user={"username": "admin_test"},
    )
    assert added["update_type"] == "server_reconnect"
    assert added["target"] == "Musik & Relay"

    added_reflash = changelog_service.add_server_update(
        title="Patch Firmware Audio Driver",
        description="Perubahan I2S codec.",
        update_type="reflash",
        target="Firmware ESP32",
        admin_user={"username": "admin_test"},
    )
    assert added_reflash["update_type"] == "reflash"

    all_entries = changelog_service.list_server_updates()
    assert len(all_entries) == 3

    assert changelog_service.delete_server_update(added["id"]) is True
    assert changelog_service.delete_server_update("srv_nonexistent") is False
    assert len(changelog_service.list_server_updates()) == 2


def test_unified_changelog_payload_redaction(isolated_env):
    payload = changelog_service.get_unified_changelog_payload()
    # board_alpha, board_beta, plus auto-merged DEFAULT_PRESETS['esp32s3_cam']
    assert payload["stats"]["total_presets"] == 3
    assert payload["stats"]["total_firmware_releases"] == 4
    assert payload["stats"]["total_server_updates"] == 1

    raw_dump = json.dumps(payload)
    assert "private-bucket" not in raw_dump
    assert "local-private" not in raw_dump
    assert "firmware_presets/" not in raw_dump
    assert "protected_assets/" not in raw_dump
    assert "secret/alpha.enc" not in raw_dump
    assert "XZ-PRESET-SECRET-1234" not in raw_dump
    assert "beta_owner" not in raw_dump
    assert "a" * 64 not in raw_dump

    alpha = next(p for p in payload["presets"] if p["id"] == "board_alpha")
    assert alpha["versions"][0]["update_type"] == "reflash"
    assert alpha["versions"][0]["sha256_short"] == "a" * 12

    beta = next(p for p in payload["presets"] if p["id"] == "board_beta")
    # Versions sorted descending (v002 first)
    assert beta["versions"][0]["version"] == "v002"
    assert beta["versions"][0]["update_type"] == "reconnect"


def test_preset_update_type_changelog_edit_and_authorized_list(isolated_env):
    admin = {"id": 1, "username": "admin", "role": "admin"}
    owner = {"id": 42, "username": "BETA_OWNER", "role": "user"}
    stranger = {"id": 99, "username": "stranger", "role": "user"}

    assert preset_approval_service.get_user_authorized_presets(None, ["board_alpha", "board_beta"]) == []
    assert preset_approval_service.get_user_authorized_presets(admin, ["board_alpha", "board_beta"]) == ["board_alpha", "board_beta"]
    assert preset_approval_service.get_user_authorized_presets(owner, ["board_alpha", "board_beta"]) == ["board_beta"]
    assert preset_approval_service.get_user_authorized_presets(stranger, ["board_alpha", "board_beta"]) == []

    saved = preset_approval_service.save_or_update_preset(
        preset_id="board_alpha",
        title="Board Alpha ESP32-S3",
        chip="ESP32-S3",
        offset="0x0",
        description="Updated",
        file_bytes=b"\xe9\x00\x01\x02" * 64,
        filename="alpha_v002.bin",
        admin_user=admin,
        changelog="Fix ST7789 backlight flicker",
        update_type="reconnect",
    )
    assert saved["active_version"] == "v002"
    v2 = next(v for v in saved["versions"] if v["version"] == "v002")
    assert v2["update_type"] == "reconnect"

    edited = preset_approval_service.update_preset_version_changelog(
        preset_id="board_alpha",
        version="v001",
        changelog="Catatan v001 diperbarui oleh admin.",
        update_type="reflash",
        admin_user=admin,
    )
    v1 = next(v for v in edited["versions"] if v["version"] == "v001")
    assert v1["changelog"] == "Catatan v001 diperbarui oleh admin."
    assert v1["update_type"] == "reflash"

    with pytest.raises(ValueError):
        preset_approval_service.update_preset_version_changelog("board_alpha", "v999", "abc")
    with pytest.raises(ValueError):
        preset_approval_service.update_preset_version_changelog("board_alpha", "v001", "   ")


def test_changelog_routes_and_api(isolated_env, monkeypatch):
    app = _build_test_app()
    monkeypatch.setattr(chat_router_mod, "get_current_user", lambda req: None)
    client = TestClient(app)

    for path in ("/changelog", "/info-update"):
        resp = client.get(path)
        assert resp.status_code == 200
        html = resp.text
        assert "Info Update &amp; Changelog" in html or "Info Update & Changelog" in html
        assert "Wajib Flash Ulang (.bin)" in html
        assert "Otomatis / Cukup Reconnect" in html
        assert "Perbaikan panjang buffer musik" in html
        assert "/web-flasher?preset=board_beta&amp;version=v002" in html or "/web-flasher?preset=board_beta&version=v002" in html
        assert 'href="/changelog"' in html
        assert "XZ-PRESET-SECRET-1234" not in html
        assert "private-bucket" not in html

    api_resp = client.get("/api/v1/changelog")
    assert api_resp.status_code == 200
    body = api_resp.json()
    assert body["success"] is True
    assert body["stats"]["total_presets"] == 3
    assert len(body["server_updates"]) == 1


def test_web_flasher_smart_default_unauth_and_changelog_box(isolated_env, monkeypatch):
    app = _build_test_app()
    client = TestClient(app)

    # 1. Guest / expired session: sees #boxPresetLoginRequired visible, #boxPresetUnlocked & #boxPresetLocked hidden
    monkeypatch.setattr(chat_router_mod, "get_current_user", lambda req: None)
    resp_guest = client.get("/web-flasher")
    assert resp_guest.status_code == 200
    html_guest = resp_guest.text
    assert 'id="verDetailChangelog"' in html_guest
    assert 'id="verDetailDate"' in html_guest
    assert 'id="verUpdateActionBadge"' in html_guest
    assert 'id="versionStatusPill"' in html_guest
    assert 'id="olderVersionAlert"' in html_guest
    assert 'id="boxPresetLoginRequired" style=""' in html_guest
    assert 'id="boxPresetLocked" style="display:none;"' in html_guest
    assert 'Perlu Masuk Akun' in html_guest

    # 2. Logged-in user who owns board_beta (not board_alpha): smart default selects board_beta!
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


def test_admin_changelog_and_update_type_endpoints(isolated_env, monkeypatch):
    app = _build_test_app()
    admin = {"id": 1, "username": "admin_user", "role": "admin"}
    normal_user = {"id": 2, "username": "beta_owner", "role": "user"}
    client = TestClient(app)

    # Non-admin blocked
    monkeypatch.setattr("xiaozhi.dependencies.get_current_user", lambda req: normal_user)
    monkeypatch.setattr(admin_fw_router_mod, "require_admin", lambda req: (_ for _ in ()).throw(chat_router_mod.HTTPException(status_code=403)))
    blocked = client.post(
        "/admin/firmware/server-changelog/add",
        data={"title": "X", "description": "Y", "csrf_token": "bad"},
        follow_redirects=False,
    )
    assert blocked.status_code == 403

    # Admin authenticated
    monkeypatch.setattr(admin_fw_router_mod, "require_admin", lambda req: admin)
    valid_csrf = make_csrf_token(admin)

    # Invalid CSRF must fail 403
    bad_csrf = client.post(
        "/admin/firmware/server-changelog/add",
        data={"title": "X", "description": "Y", "csrf_token": "tampered"},
        follow_redirects=False,
    )
    assert bad_csrf.status_code == 403

    # Upload new preset version with update_type=reconnect
    save_resp = client.post(
        "/admin/firmware/presets/save",
        data={
            "preset_id": "board_alpha",
            "title": "Board Alpha ESP32-S3",
            "chip": "ESP32-S3",
            "offset": "0x0",
            "description": "Updated desc",
            "changelog": "Pembaruan minor kompatibel",
            "update_type": "reconnect",
            "csrf_token": valid_csrf,
        },
        files={"firmware_file": ("alpha_v002.bin", b"\xe9\x01\x02\x03" * 32, "application/octet-stream")},
        follow_redirects=False,
    )
    assert save_resp.status_code == 303
    alpha_after = preset_approval_service.get_preset("board_alpha")
    assert alpha_after["active_version"] == "v002"
    v2_alpha = next(v for v in alpha_after["versions"] if v["version"] == "v002")
    assert v2_alpha["update_type"] == "reconnect"

    # Inline changelog edit for existing v001
    edit_resp = client.post(
        "/admin/firmware/presets/board_alpha/versions/v001/changelog",
        data={
            "changelog": "Revisi teks v001",
            "update_type": "reflash",
            "csrf_token": valid_csrf,
        },
        follow_redirects=False,
    )
    assert edit_resp.status_code == 303
    v1_alpha = next(v for v in preset_approval_service.get_preset("board_alpha")["versions"] if v["version"] == "v001")
    assert v1_alpha["changelog"] == "Revisi teks v001"
    assert v1_alpha["update_type"] == "reflash"

    # Add & delete server changelog entry
    add_srv = client.post(
        "/admin/firmware/server-changelog/add",
        data={
            "title": "Rilis Server Tool Recall",
            "description": "Sekarang bot mengingat topik lama lebih akurat.",
            "update_type": "server_reconnect",
            "target": "Memory & MCP",
            "csrf_token": valid_csrf,
        },
        follow_redirects=False,
    )
    assert add_srv.status_code == 303
    entries = changelog_service.list_server_updates()
    created = next(e for e in entries if e["title"] == "Rilis Server Tool Recall")
    assert created["update_type"] == "server_reconnect"

    del_srv = client.post(
        f"/admin/firmware/server-changelog/{created['id']}/delete",
        data={"csrf_token": valid_csrf},
        follow_redirects=False,
    )
    assert del_srv.status_code == 303
    assert all(e["id"] != created["id"] for e in changelog_service.list_server_updates())

    # Admin approvals page renders Tab 4 & inline editor cleanly
    # Avoid DB access in get_approval_service if unused
    class _FakeApprovals:
        def get_pending_approvals(self):
            return []

    monkeypatch.setattr(admin_fw_router_mod, "get_approval_service", lambda: _FakeApprovals())
    page_resp = client.get("/admin/firmware/approvals")
    assert page_resp.status_code == 200
    assert "Changelog Server &amp; MCP" in page_resp.text or "Changelog Server & MCP" in page_resp.text
    assert "Simpan Changelog" in page_resp.text
