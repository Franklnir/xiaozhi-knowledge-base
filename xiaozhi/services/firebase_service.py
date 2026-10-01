"""Fail-closed compatibility shim for the retired password-based Firebase sync.

Local Xiaozhi credentials must never be replayed to another identity provider.
Firebase provisioning can be reintroduced only with Admin SDK/custom tokens and
an explicit account-linking flow.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("xiaozhi.services.firebase")


def get_firebase_email(username: str, email: Optional[str] = None) -> str:
    """Normalize a username/email for legacy callers without making a request."""
    if email and "@" in email:
        return email.strip().lower()
    if "@" in username:
        return username.strip().lower()
    clean = "".join(c for c in username.lower() if c.isalnum() or c in (".", "_"))
    return f"{clean}@xiaozhi.biz.id"


def sync_firebase_user(
    username: str,
    password: Optional[str] = None,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
    is_register: bool = False,
) -> Optional[Dict[str, Any]]:
    """Retained for compatibility; intentionally performs no password sync."""
    del username, password, email, display_name, is_register
    logger.warning("Password-based Firebase synchronization is disabled for security.")
    return None
