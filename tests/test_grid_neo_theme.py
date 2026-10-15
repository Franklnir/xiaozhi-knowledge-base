import pytest
from xiaozhi.config import UI_THEMES
from xiaozhi.database.sqlite_store import SQLiteStore
import tempfile
import os

def test_ui_themes_contains_grid_neo():
    assert "grid_neo" in UI_THEMES
    assert "neo" in UI_THEMES
    assert "dark" in UI_THEMES
    assert "default" in UI_THEMES

def test_sqlite_store_theme_normalization():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        store = SQLiteStore(db_path=db_path)
        try:
            user = store.create_user("testuser", "password123")
            user_id = user["id"]

            # Default theme should be neo
            assert user.get("ui_theme") == "neo"

            # Setting grid_neo
            updated = store.set_user_theme(user_id, "grid_neo")
            assert updated.get("ui_theme") == "grid_neo"

            # Setting 'grid neo' with space should normalize to 'grid_neo'
            updated2 = store.set_user_theme(user_id, "grid neo")
            assert updated2.get("ui_theme") == "grid_neo"

            # Setting 'grid-neo' with hyphen should normalize to 'grid_neo'
            updated3 = store.set_user_theme(user_id, "grid-neo")
            assert updated3.get("ui_theme") == "grid_neo"

            # Setting invalid theme should raise ValueError
            with pytest.raises(ValueError):
                store.set_user_theme(user_id, "invalid_theme_xyz")
        finally:
            store.close()

def test_json_store_theme_normalization(monkeypatch):
    from xiaozhi.database.store import HFJsonStore
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("HF_DB_CACHE_DIR", tmpdir)
        monkeypatch.setenv("HF_DB_LOCAL_ONLY", "1")
        monkeypatch.delenv("HF_TOKEN", raising=False)
        store = HFJsonStore()
        user = store.create_user("jsonuser", "password123")
        user_id = user["id"]

        assert user.get("ui_theme") == "neo"

        updated = store.set_user_theme(user_id, "grid neo")
        assert updated.get("ui_theme") == "grid_neo"

        updated2 = store.set_user_theme(user_id, "grid-neo")
        assert updated2.get("ui_theme") == "grid_neo"

def test_base_template_theme_render():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("base.html")
    
    # Render with grid_neo user
    rendered = template.render(
        user={"id": 1, "username": "ajeng", "role": "user", "ui_theme": "grid_neo"},
        csrf_token="dummy_token",
        request=None
    )
    assert 'data-theme="grid_neo"' in rendered
    assert '<option value="grid_neo">Grid Neo</option>' in rendered
    assert ':root[data-theme="grid_neo"]' in rendered

