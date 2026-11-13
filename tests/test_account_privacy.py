import logging
import re
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from xiaozhi.core.security import verify_password
from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.dependencies import SESSION_COOKIE


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    from xiaozhi import dependencies, main
    from xiaozhi.mcp import server
    from xiaozhi.services import firebase_service, reminder_service

    store = SQLiteStore(str(tmp_path / "account_privacy.db"))
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(dependencies, "_store", store)
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "")
    monkeypatch.setattr(main, "ADMIN_USERNAME", "audit_admin")
    monkeypatch.setattr(main, "RESTORED_USER_USERNAME", "")
    monkeypatch.setattr(main, "init_task_queue", AsyncMock())
    monkeypatch.setattr(reminder_service, "check_reminders_periodically", AsyncMock())
    monkeypatch.setattr(server, "init_mcp_server", lambda *_: None)
    monkeypatch.setattr(firebase_service, "sync_firebase_user", lambda *_, **__: None)
    return main, store


def test_public_login_preserves_login_without_disclosing_matching_accounts(isolated_app):
    main, store = isolated_app
    username = "private_test_account"
    password = "synthetic-login-password"
    store.create_user(username, password)

    with TestClient(main.app, follow_redirects=False) as client:
        search = client.get("/accounts/search", params={"q": "private"})
        assert search.status_code == 404
        assert username not in search.text

        login = client.get("/login?mode=search")
        assert login.status_code == 200
        assert "Bantuan atau lupa akun?" in login.text
        assert "accounts/search" not in login.text
        assert 'value="search"' not in login.text
        assert username not in login.text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)

        response = client.post("/login", data={
            "username": username,
            "password": password,
            "csrf_token": csrf,
        })
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard"
        assert SESSION_COOKIE in response.cookies


def test_startup_requires_configured_password_to_create_admin(isolated_app, monkeypatch, caplog):
    main, store = isolated_app
    password = "synthetic-admin-password"
    caplog.set_level(logging.INFO)

    with TestClient(main.app):
        assert not store.has_admin_user()
        assert "Set ADMIN_PASSWORD" in caplog.text
        assert "generated secure password" not in caplog.text

    monkeypatch.setattr(main, "ADMIN_PASSWORD", password)
    with TestClient(main.app):
        assert store.has_admin_user()
        admin_before = store.get_user_by_username("audit_admin")
        assert verify_password(password, admin_before["password_hash"])
    assert password not in caplog.text

    monkeypatch.setattr(main, "ADMIN_PASSWORD", "")
    with TestClient(main.app):
        assert store.get_user_by_username("audit_admin") == admin_before
