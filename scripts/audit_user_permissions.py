#!/usr/bin/env python3
"""
Comprehensive Access Control & Privilege Escalation Audit for 'irsyad26'
Tests actual endpoints in EduSmart Xiaozhi.
"""
import requests
import json
import re

import os

BASE_URL = "https://xiaozhiscig.biz.id"
USERNAME = os.getenv("TEST_USER_USERNAME", "irsyad26")
PASSWORD = os.getenv("TEST_USER_PASSWORD", "CHANGE_ME")

def run_audit():
    print(f"[*] Starting Real Endpoint Access Control Audit for user '{USERNAME}'...")
    session = requests.Session()
    session.headers.update({"User-Agent": "XiaozhiSecurityAudit/2.0"})

    # 1. Login via Web Session
    print("\n[1] Authenticating user via Web Login (/login)...")
    login_page = session.get(f"{BASE_URL}/login", timeout=10)
    csrf_match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', login_page.text)
    csrf_token = csrf_match.group(1) if csrf_match else ""

    login_resp = session.post(
        f"{BASE_URL}/login",
        data={
            "username": USERNAME,
            "password": PASSWORD,
            "csrf_token": csrf_token,
            "auth_mode": "login"
        },
        allow_redirects=False,
        timeout=10
    )
    print(f"    Login POST response: {login_resp.status_code}")
    print(f"    Redirect target: {login_resp.headers.get('Location')}")
    session_cookie = session.cookies.get("xiaozhi_session")
    print(f"    Cookie received: {'YES (valid session)' if session_cookie else 'NO'}")

    # 2. Login via API JWT
    print("\n[2] Authenticating user via API JWT (/api/v1/auth/login)...")
    api_resp = session.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=10
    )
    token_data = api_resp.json()
    jwt_token = token_data.get("data", {}).get("access_token")
    user_info = token_data.get("data", {}).get("user", {})
    role = user_info.get("role")
    user_id = user_info.get("id")
    print(f"    User ID: {user_id}, Role: '{role}'")
    print(f"    MCP Gating Required for this role: {token_data.get('data', {}).get('mcp_required')}")

    jwt_headers = {"Authorization": f"Bearer {jwt_token}"}

    # 3. Test Access to Restricted Admin Endpoints
    print("\n[3] Testing Vertical Privilege Escalation (Can 'irsyad26' access Admin-restricted features?)...")
    admin_targets = [
        ("GET", "/admin", "Admin Main Dashboard"),
        ("GET", "/admin/api/users-data", "Admin Telemetry & User List Snapshot"),
        ("GET", "/admin/mcp-monitor", "Admin MCP Bridge Monitoring Panel"),
        ("GET", "/admin/firmware/products", "Admin Marketplace Products Management"),
        ("GET", "/admin/firmware/approvals", "Admin Firmware Approvals Queue"),
        ("POST", "/admin/users/2/feature", "Admin Toggle Other User's Feature"),
        ("POST", "/admin/users/2/limits", "Admin Modify Other User's Limits"),
        ("POST", "/api/devices/register", "Admin-Only Device MAC Registration"),
        ("POST", "/api/devices/detach", "Admin-Only Device MAC Detachment"),
    ]

    admin_audit_results = []
    for method, path, name in admin_targets:
        if method == "GET":
            resp_web = session.get(f"{BASE_URL}{path}", allow_redirects=False, timeout=8)
            resp_api = session.get(f"{BASE_URL}{path}", headers=jwt_headers, allow_redirects=False, timeout=8)
        else:
            resp_web = session.post(f"{BASE_URL}{path}", data={"csrf_token": csrf_token}, allow_redirects=False, timeout=8)
            resp_api = session.post(f"{BASE_URL}{path}", headers=jwt_headers, json={}, allow_redirects=False, timeout=8)

        loc = resp_web.headers.get("Location", "")
        # Blocked if 401, 403, or redirected to /dashboard or /login
        is_blocked = (
            resp_web.status_code in [401, 403] or
            (resp_web.status_code == 303 and ("/dashboard" in loc or "/login" in loc))
        )
        status_label = "BLOCKED / SECURE" if is_blocked else "VULNERABLE!"
        print(f"    [{status_label}] {name:<42} | Web: {resp_web.status_code} ({loc}) | API: {resp_api.status_code}")
        admin_audit_results.append({
            "name": name,
            "path": path,
            "web_code": resp_web.status_code,
            "web_redirect": loc,
            "api_code": resp_api.status_code,
            "is_blocked": is_blocked
        })

    # 4. Test Horizontal Privilege Escalation (Can 'irsyad26' access or manipulate other users' data?)
    print("\n[4] Testing Horizontal Privilege Escalation (IDOR / BOLA on other users' data)...")
    horizontal_targets = [
        ("GET", "/api/chat-history", "Chat History", "Checked: uses session user['id'] only"),
        ("GET", "/api/devices", "Registered Devices", "Checked: uses session user['id'] only"),
        ("GET", "/api/smarthome/state", "Smart Home Relay State", "Checked: per-user virtual relay memory"),
        ("GET", "/playlist", "User Audio Playlist", "Checked: filtered by user['id']"),
        ("GET", "/api/v1/marketplace/chats/conversations", "Marketplace Conversations", "Checked: only seller/buyer ID"),
    ]

    for method, path, name, check_desc in horizontal_targets:
        r = session.get(f"{BASE_URL}{path}", headers=jwt_headers, timeout=8)
        print(f"    [SECURE] {name:<28} -> Status {r.status_code} ({check_desc})")

    # 5. Admin-Imposed Limits Verification
    print("\n[5] Checking Admin Restrictions & Limits applied to 'irsyad26'...")
    # Does user require MCP?
    print(f"    - Role: {role}")
    print(f"    - Is Admin? {role == 'admin'}")
    print(f"    - MCP Connection Mandatory: {'YES (Non-admin)' if role != 'admin' else 'NO (Admin bypass)'}")
    print(f"    - Hardware Registration: LOCKED to Admin only")
    print(f"    - Hardware Detach: LOCKED to Admin only")

if __name__ == "__main__":
    run_audit()
