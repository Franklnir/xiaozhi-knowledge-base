import pytest
from xiaozhi.mcp.bridge import (
    validate_mcp_ws_url,
    record_bridge_failure,
    reset_bridge_failure,
    is_bridge_in_cooldown,
    mcp_bridge_failure_counts,
    mcp_bridge_failure_cooldowns,
)
import xiaozhi.config as config

def test_validate_mcp_ws_url(monkeypatch):
    monkeypatch.setattr(config, "IS_PRODUCTION", True)

    # Valid Xiaozhi endpoint
    valid_url = "wss://api.xiaozhi.me/mcp/?token=test_token_123"
    is_valid, msg = validate_mcp_ws_url(valid_url)
    assert is_valid is True
    assert msg == ""

    # Invalid scheme
    http_url = "http://api.xiaozhi.me/mcp"
    is_valid, msg = validate_mcp_ws_url(http_url)
    assert is_valid is False
    assert "wss://" in msg

    # Web console URL (mistakenly copied by user)
    console_url = "wss://xiaozhi.me/console/agents"
    is_valid, msg = validate_mcp_ws_url(console_url)
    assert is_valid is False
    assert "web console" in msg.lower()

    # Private IP / LAN URL in production
    lan_url = "wss://172.16.23.28:8015/mcp"
    is_valid, msg = validate_mcp_ws_url(lan_url)
    assert is_valid is False
    assert "privat lokal" in msg.lower()

    lan_url_192 = "wss://192.168.1.100/mcp"
    is_valid, msg = validate_mcp_ws_url(lan_url_192)
    assert is_valid is False

    lan_url_10 = "wss://10.0.0.1:8000/mcp"
    is_valid, msg = validate_mcp_ws_url(lan_url_10)
    assert is_valid is False

def test_bridge_cooldown_and_backoff():
    task_key = "99999_1"
    reset_bridge_failure(task_key)

    assert not is_bridge_in_cooldown(task_key)

    # 1st failure (count=1 -> 30s)
    cd1 = record_bridge_failure(task_key, max_cooldown=300)
    assert cd1 == 30
    assert is_bridge_in_cooldown(task_key)
    assert mcp_bridge_failure_counts[task_key] == 1

    # 2nd failure (count=2 -> 60s)
    cd2 = record_bridge_failure(task_key, max_cooldown=300)
    assert cd2 == 60

    # 3rd failure (count=3 -> 120s)
    cd3 = record_bridge_failure(task_key, max_cooldown=300)
    assert cd3 == 120

    # Reset
    reset_bridge_failure(task_key)
    assert not is_bridge_in_cooldown(task_key)
    assert task_key not in mcp_bridge_failure_counts
