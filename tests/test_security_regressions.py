import asyncio
import time

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
from xiaozhi.routers import api_v1_auth
from xiaozhi.routers.youtube import _require_device_auth


def make_request(headers=None, query_string=b""):
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
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
    class Response:
        ok = True

        @staticmethod
        def json():
            return {
                "sub": "google-user",
                "email": "verified@example.test",
                "email_verified": "true",
                "aud": "wrong-client",
                "iss": "https://accounts.google.com",
            }

    monkeypatch.setattr(api_v1_auth, "GOOGLE_AUTH_ENABLED", True)
    monkeypatch.setattr(api_v1_auth, "GOOGLE_CLIENT_ID", "expected-client")
    monkeypatch.setattr(api_v1_auth.requests, "get", lambda *args, **kwargs: Response())
    body = api_v1_auth.GoogleAuthRequest(id_token="opaque-id-token", action="login")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api_v1_auth.api_google_auth(body, make_request()))
    assert exc.value.status_code == 502
