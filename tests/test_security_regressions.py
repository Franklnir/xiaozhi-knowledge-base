import asyncio
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from xiaozhi import dependencies
from xiaozhi.config import JWT_SECRET, csrf_serializer
from xiaozhi.core.security import (
    JWT_ALGORITHM,
    JWT_AUDIENCE,
    JWT_ISSUER,
    create_access_token,
    validate_access_token,
)
from xiaozhi.marketplace.payments import midtrans, simulator
from xiaozhi.marketplace import payments
from xiaozhi.mcp.bridge import _safe_endpoint_for_log
from xiaozhi.routers import api_v1_auth
from xiaozhi.routers import auth, google_auth
from xiaozhi.routers.youtube import _require_device_auth


def make_request(headers=None, query_string=b"", method="GET", path="/"):
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query_string,
            "headers": raw_headers,
            "client": ("127.0.0.1", 12345),
            "scheme": "https",
            "server": ("testserver", 443),
        }
    )


class FakeUserStore:
    def __init__(self, session_version=1):
        self.session_version = session_version

    def get_user(self, user_id):
        return {
            "id": int(user_id),
            "username": "alice",
            "role": "user",
            "session_version": self.session_version,
        }


class FakeDeviceStore:
    def find_user_by_mcp_token(self, token):
        if token != "device-secret":
            return None
        return {"user_id": 7, "board_mac": "AA:BB:CC:DD:EE:FF"}


def test_jwt_has_issuer_audience_and_session_version(monkeypatch):
    token = create_access_token(1, "alice", "user", session_version=4)
    payload, error = validate_access_token(token)
    assert error is None
    assert payload["session_version"] == 4

    monkeypatch.setattr(dependencies, "_store", FakeUserStore(session_version=5))
    request = make_request({"Authorization": f"Bearer {token}"})
    assert dependencies.get_current_user(request) is None


def test_jwt_rejects_wrong_audience():
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "1",
            "username": "alice",
            "role": "user",
            "sv": 1,
            "type": "access",
            "iat": now,
            "exp": now + 60,
            "iss": JWT_ISSUER,
            "aud": "another-service",
        },
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    payload, error = validate_access_token(token)
    assert payload is None
    assert error


def test_authenticated_csrf_rejects_anonymous_token():
    token = csrf_serializer.dumps({"uid": None, "nonce": "test"})
    with pytest.raises(HTTPException) as exc:
        dependencies.validate_csrf(make_request(), token, {"id": 9})
    assert exc.value.status_code == 403


def test_device_endpoint_requires_header_and_bound_mac():
    store = FakeDeviceStore()
    with pytest.raises(HTTPException) as exc:
        _require_device_auth(store, make_request(query_string=b"token=device-secret"))
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        _require_device_auth(
            store,
            make_request({"X-Device-Token": "device-secret", "X-Device-Mac": "11:22:33:44:55:66"}),
        )
    assert exc.value.status_code == 403

    owner_id, mac = _require_device_auth(
        store,
        make_request({"X-Device-Token": "device-secret", "X-Device-Mac": "AA-BB-CC-DD-EE-FF"}),
    )
    assert owner_id == 7
    assert mac == "AA-BB-CC-DD-EE-FF"


def test_mcp_log_endpoint_never_contains_query_credentials():
    endpoint = "wss://api.xiaozhi.me/mcp/?token=secret-value&key=another-secret"

    safe = _safe_endpoint_for_log(endpoint)

    assert safe == "wss://api.xiaozhi.me"
    assert "secret-value" not in safe
    assert "another-secret" not in safe


def test_payment_providers_fail_closed(monkeypatch):
    monkeypatch.setattr(payments, "PAYMENTS_ENABLED", False)
    with pytest.raises(RuntimeError):
        payments.get_payment_provider("simulator")

    monkeypatch.setattr(simulator, "IS_PRODUCTION", True)
    monkeypatch.setattr(simulator, "ALLOW_SIMULATOR_PAYMENTS", False)
    with pytest.raises(RuntimeError):
        simulator.SimulatorPaymentProvider()

    monkeypatch.setattr(midtrans, "MIDTRANS_SERVER_KEY", "")
    with pytest.raises(RuntimeError):
        midtrans.MidtransPaymentProvider()


def test_google_id_token_rejects_wrong_audience(monkeypatch):
    captured = {}

    def reject_wrong_audience(_token, _request, audience, **_kwargs):
        captured["audience"] = audience
        raise ValueError("wrong audience")

    monkeypatch.setattr(api_v1_auth, "GOOGLE_AUTH_ENABLED", True)
    monkeypatch.setattr(api_v1_auth, "GOOGLE_CLIENT_ID", "expected-client")
    monkeypatch.setattr(api_v1_auth.google_id_token, "verify_oauth2_token", reject_wrong_audience)
    body = api_v1_auth.GoogleAuthRequest(id_token="opaque-id-token", action="login")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api_v1_auth.api_google_auth(body, make_request()))
    assert exc.value.status_code == 401
    assert captured["audience"] == "expected-client"
    assert "wrong audience" not in str(exc.value.detail)


def test_session_cookie_is_secure_httponly_and_same_site(monkeypatch):
    monkeypatch.setattr(auth, "IS_PRODUCTION", True)
    response = auth.RedirectResponse("/dashboard", status_code=303)

    auth.set_session_cookie(
        response,
        make_request(),
        {"id": 1, "username": "alice", "session_version": 2},
    )

    cookie_headers = "\n".join(response.headers.getlist("set-cookie")).lower()
    assert "httponly" in cookie_headers
    assert "secure" in cookie_headers
    assert "samesite=lax" in cookie_headers
    assert "path=/" in cookie_headers
    assert "domain=" not in cookie_headers


def test_google_authorization_uses_pkce_and_oidc_nonce(monkeypatch):
    monkeypatch.setattr(google_auth, "GOOGLE_AUTH_ENABLED", True)
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_ID", "client.apps.googleusercontent.com")
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_SECRET", "test-secret")

    response = asyncio.run(google_auth.google_login(make_request(), intent="login", source="web"))
    query = parse_qs(urlparse(response.headers["location"]).query)
    cookies = "\n".join(response.headers.getlist("set-cookie"))

    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    assert query["nonce"][0]
    assert query["scope"] == ["openid email profile"]
    assert google_auth.OAUTH_STATE_COOKIE in cookies
    assert google_auth.OAUTH_PKCE_COOKIE in cookies
    assert "HttpOnly" in cookies


def _google_callback_request(state_nonce: str, verifier: str):
    cookie = (
        f"{google_auth.OAUTH_STATE_COOKIE}={state_nonce}; "
        f"{google_auth.OAUTH_PKCE_COOKIE}={verifier}"
    )
    return make_request(
        {"Cookie": cookie},
        path="/api/auth/google/callback",
    )


def test_google_callback_rejects_id_token_nonce_mismatch(monkeypatch):
    state_nonce = "state-nonce"
    verifier = "v" * 64
    state = google_auth.google_oauth_serializer.dumps(
        {
            "action": "login",
            "source": "web",
            "nonce": state_nonce,
            "oidc_nonce": "expected-oidc-nonce",
            "pkce_challenge": google_auth._pkce_challenge(verifier),
        }
    )

    class TokenResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"id_token": "opaque-id-token"}

    captured_payload = {}

    def fake_post(_url, data, timeout):
        captured_payload.update(data)
        return TokenResponse()

    monkeypatch.setattr(google_auth, "GOOGLE_AUTH_ENABLED", True)
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_ID", "client.apps.googleusercontent.com")
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(google_auth.requests, "post", fake_post)
    monkeypatch.setattr(
        google_auth.google_id_token,
        "verify_oauth2_token",
        lambda *args, **kwargs: {
            "sub": "google-subject",
            "email": "alice@example.test",
            "email_verified": True,
            "nonce": "wrong-oidc-nonce",
        },
    )

    response = asyncio.run(
        google_auth.google_callback(
            _google_callback_request(state_nonce, verifier),
            code="authorization-code",
            state=state,
            error=None,
        )
    )

    assert response.status_code == 303
    assert "tidak+valid" in response.headers["location"]
    assert captured_payload["code_verifier"] == verifier
    cleared = "\n".join(response.headers.getlist("set-cookie"))
    assert f"{google_auth.OAUTH_STATE_COOKIE}=" in cleared
    assert f"{google_auth.OAUTH_PKCE_COOKIE}=" in cleared


def test_google_token_exchange_error_does_not_log_response_secret(monkeypatch, caplog):
    state_nonce = "state-nonce"
    verifier = "p" * 64
    state = google_auth.google_oauth_serializer.dumps(
        {
            "action": "login",
            "source": "web",
            "nonce": state_nonce,
            "oidc_nonce": "oidc-nonce",
            "pkce_challenge": google_auth._pkce_challenge(verifier),
        }
    )

    class TokenResponse:
        ok = False
        status_code = 400

        @staticmethod
        def json():
            return {"error": "invalid_grant", "error_description": "LEAK-ME-NOT"}

    monkeypatch.setattr(google_auth, "GOOGLE_AUTH_ENABLED", True)
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_ID", "client.apps.googleusercontent.com")
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(google_auth.requests, "post", lambda *args, **kwargs: TokenResponse())

    with caplog.at_level("ERROR"):
        response = asyncio.run(
            google_auth.google_callback(
                _google_callback_request(state_nonce, verifier),
                code="authorization-code",
                state=state,
                error=None,
            )
        )

    assert response.status_code == 303
    assert "LEAK-ME-NOT" not in caplog.text
    assert "invalid_grant" in caplog.text


def test_google_login_never_auto_links_local_account_by_email(monkeypatch):
    state_nonce = "state-nonce"
    verifier = "q" * 64
    oidc_nonce = "oidc-nonce"
    state = google_auth.google_oauth_serializer.dumps(
        {
            "action": "login",
            "source": "web",
            "nonce": state_nonce,
            "oidc_nonce": oidc_nonce,
            "pkce_challenge": google_auth._pkce_challenge(verifier),
        }
    )

    class TokenResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"id_token": "opaque-id-token"}

    class LocalAccountStore:
        link_called = False

        @staticmethod
        def get_user_by_google_id(_google_id):
            return None

        @staticmethod
        def get_user_by_email(_email):
            return {"id": 7, "username": "local-user"}

        def link_google_account(self, *_args):
            self.link_called = True

    store = LocalAccountStore()
    monkeypatch.setattr(google_auth, "GOOGLE_AUTH_ENABLED", True)
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_ID", "client.apps.googleusercontent.com")
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(google_auth.requests, "post", lambda *args, **kwargs: TokenResponse())
    monkeypatch.setattr(google_auth, "get_store", lambda: store)
    monkeypatch.setattr(
        google_auth.google_id_token,
        "verify_oauth2_token",
        lambda *args, **kwargs: {
            "sub": "unlinked-google-subject",
            "email": "local@example.test",
            "email_verified": True,
            "nonce": oidc_nonce,
        },
    )

    response = asyncio.run(
        google_auth.google_callback(
            _google_callback_request(state_nonce, verifier),
            code="authorization-code",
            state=state,
            error=None,
        )
    )

    assert response.status_code == 303
    assert "belum+ditautkan" in response.headers["location"]
    assert store.link_called is False
