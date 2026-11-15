import asyncio
import re
import threading
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from xiaozhi.config import SESSION_COOKIE, session_serializer
from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.dependencies import make_csrf_token
from xiaozhi.main import app
from xiaozhi.routers import auth, dashboard


def test_login_uses_light_layout_and_has_unique_request_ids(monkeypatch):
    monkeypatch.setattr(auth, "get_current_user", lambda request: None)
    client = TestClient(app)
    first = client.get("/login", headers={"X-Request-ID": "caller-value"})
    second = client.get("/login?mode=register", headers={"X-Request-ID": "caller-value"})
    assert first.status_code == second.status_code == 200
    assert re.fullmatch(r"req-[0-9a-f]{32}", first.headers["X-Request-ID"])
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    assert "auth;dur=" in first.headers["Server-Timing"]
    assert "render;dur=" in first.headers["Server-Timing"]
    assert first.headers["Cache-Control"] == "no-store"
    assert 'data-theme="playful"' in first.text
    assert "/static/js/login.js" in first.text
    assert "/static/js/app.js" not in first.text
    assert "/static/img/apple-touch-icon.png" in first.text
    assert "/static/css/claymorphism.css" not in first.text
    assert "wa.me/" in first.text and "produk.irsyadlabs.id" in first.text
    assert client.get("/alamat-tidak-ada").headers["X-Request-ID"].startswith("req-")


def test_unhandled_error_preserves_request_id(monkeypatch):
    def unavailable(request):
        raise RuntimeError("internal test detail")

    monkeypatch.setattr(auth, "get_current_user", unavailable)
    response = TestClient(app, raise_server_exceptions=False).get("/login")
    assert response.status_code == 500
    assert response.headers["X-Request-ID"].startswith("req-")
    assert "internal test detail" not in response.text


def test_login_failure_logs_stages_and_returns_request_id(monkeypatch):
    monkeypatch.setattr(auth, "enforce_predefined_limit", lambda *args: None)

    class Store:
        def get_user_by_username(self, username):
            return None

    monkeypatch.setattr(auth, "get_store", Store)
    client = TestClient(app)
    response = client.post("/api/auth/web-login", json={
        "username": "penguji", "password": "invalid", "csrf_token": make_csrf_token(None),
    })
    assert response.status_code == 401
    assert response.headers["X-Request-ID"].startswith("req-")
    assert "db;dur=" in response.headers["Server-Timing"]
    assert "auth;dur=" in response.headers["Server-Timing"]
    assert "invalid" not in response.text


@pytest.fixture
def material_store(tmp_path, monkeypatch):
    from xiaozhi import dependencies

    store = SQLiteStore(str(tmp_path / "materials.sqlite"))
    owner = store.create_user("penguji", "password-demo-123")
    other = store.create_user("pengguna_lain", "password-demo-456")
    store.set_user_limits(owner["id"], max_materials=30)
    monkeypatch.setattr(dependencies, "_store", store)
    monkeypatch.setattr(dashboard, "get_store", lambda: store)
    return store, owner, other


def test_dashboard_search_pages_and_detail_are_scoped_to_the_owner(material_store):
    store, owner, other = material_store
    owner_id = owner["id"]
    content = "Pratinjau " * 60 + "unik_100%_di_akhir"
    first = store.add_material(owner_id, "Arsip", "Materi", content, "")
    for index in range(22):
        store.add_material(owner_id, f"Catatan {index:02}", "Tugas", "Isi materi", "")
    private = store.add_material(other["id"], "Milik akun lain", "Materi", content, "")
    client = TestClient(app)
    assert client.get(f"/api/materials/detail/{first}").status_code == 401
    client.cookies.set(SESSION_COOKIE, session_serializer.dumps({"id": owner_id, "username": owner["username"]}))

    initial = client.get("/dashboard")
    assert initial.status_code == 200
    stylesheet = re.search(r'/static/css/app-shell.css\?v=[a-f0-9]{12}', initial.text)
    assert stylesheet
    css = client.get(stylesheet.group())
    assert css.status_code == 200
    assert "immutable" in css.headers["Cache-Control"]
    assert "immutable" not in client.get("/static/css/app-shell.css?v=old").headers.get("Cache-Control", "")
    assert client.get(stylesheet.group(), headers={"If-None-Match": css.headers["ETag"]}).status_code == 304
    assert initial.headers["Cache-Control"] == "no-store"
    assert "db;dur=" in initial.headers["Server-Timing"]
    assert "render;dur=" in initial.headers["Server-Timing"]
    assert initial.text.count('class="data-row ') == 20
    assert "unik_100%_di_akhir" not in initial.text
    assert "Halaman 1 dari 2" in initial.text

    second = client.get("/api/materials/list", params={"page": 2})
    assert second.text.count('class="data-row ') == 3
    assert second.headers["Cache-Control"] == "no-store"
    assert "Halaman 2 dari 2" in second.text
    assert "unik_100%_di_akhir" not in second.text

    filtered = client.get("/api/materials/list", params={"q": "unik_100%", "category": "Materi"})
    assert filtered.text.count('class="data-row ') == 1
    assert "Arsip" in filtered.text and "Milik akun lain" not in filtered.text
    assert client.get("/api/materials/list", params={"q": "100%%"}).text.count('class="data-row ') == 0
    assert client.get(f"/api/materials/detail/{private}").status_code == 404
    own_detail = client.get(f"/api/materials/detail/{first}")
    assert own_detail.json()["content"] == content
    assert own_detail.headers["Cache-Control"] == "no-store"
    assert client.post("/api/materials/stream-index", json={"title": "tanpa CSRF"}).status_code == 403


def test_web_responds_while_bridge_reads_and_searches_wait(material_store, monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from xiaozhi.mcp import bridge
    from xiaozhi.mcp.context import mcp_active_owner_ctx
    from xiaozhi.mcp.server import _SearchToolRegistrar

    _, owner, _ = material_store
    owner_id = owner["id"]
    release = threading.Event()

    async def exercise():
        loop = asyncio.get_running_loop()
        scan_started, searches_started = asyncio.Event(), asyncio.Event()
        started = set()
        server = FastMCP("Performance test")
        registrar = _SearchToolRegistrar(server)

        def list_tokens():
            loop.call_soon_threadsafe(scan_started.set)
            assert release.wait(5)
            return []

        @registrar.tool()
        def search_news(query: str) -> str:
            def record_start():
                started.add(query)
                if len(started) == 2:
                    searches_started.set()

            loop.call_soon_threadsafe(record_start)
            assert release.wait(5)
            return f"{mcp_active_owner_ctx.get()}:{query}"

        tools = {tool.name: tool for tool in await server.list_tools()}
        assert "query" in tools["search_news"].inputSchema["properties"]
        monkeypatch.setattr(bridge.mcp_service, "mcp_reload_event", None)
        scope = mcp_active_owner_ctx.set(owner_id)
        supervisor = asyncio.create_task(bridge.mcp_background_task(Mock(list_xiaozhi_tokens=list_tokens), server))
        tasks = [asyncio.create_task(server.call_tool("search_news", {"query": str(i)})) for i in range(3)]
        try:
            await asyncio.wait_for(asyncio.gather(scan_started.wait(), searches_started.wait()), 2)
            assert started == {"0", "1"}
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                login = await asyncio.wait_for(client.get("/login"), 2)
                assert login.status_code == 200 and "playful" in login.text
                client.cookies.set(SESSION_COOKIE, session_serializer.dumps({"id": owner_id, "username": owner["username"]}))
                home, items = await asyncio.wait_for(
                    asyncio.gather(client.get("/dashboard"), client.get("/api/materials/list")),
                    2,
                )
                assert home.status_code == items.status_code == 200
                assert len({page.headers["X-Request-ID"] for page in (login, home, items)}) == 3
                assert len(started) == 2
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks[1:]), 2)
            assert [content[0].text for content, _ in results] == [f"{owner_id}:1", f"{owner_id}:2"]
        finally:
            release.set()
            supervisor.cancel()
            await asyncio.gather(supervisor, *tasks, return_exceptions=True)
            mcp_active_owner_ctx.reset(scope)

    asyncio.run(exercise())
