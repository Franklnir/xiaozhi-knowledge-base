"""Rate limiting per endpoint and IP."""
import time
from collections import defaultdict
from threading import Lock
from typing import Dict, List, Optional

from fastapi import HTTPException, Request

# Rate limit storage: {key: [timestamps]}
_rate_limits: Dict[str, List[float]] = {}
_lock = Lock()


def _cleanup_old_entries(key: str, window_seconds: int) -> None:
    """Remove expired timestamps."""
    if key not in _rate_limits:
        return
    cutoff = time.time() - window_seconds
    _rate_limits[key] = [t for t in _rate_limits[key] if t > cutoff]


def check_rate_limit(key: str, limit: int, window_seconds: int = 60) -> bool:
    """
    Check if rate limit is exceeded.
    Returns True if allowed, False if exceeded.
    """
    with _lock:
        _cleanup_old_entries(key, window_seconds)
        if key not in _rate_limits:
            _rate_limits[key] = []
        if len(_rate_limits[key]) >= limit:
            return False
        _rate_limits[key].append(time.time())
        return True


def get_rate_limit_info(key: str, limit: int, window_seconds: int = 60) -> Dict[str, int]:
    """Get current rate limit status."""
    with _lock:
        _cleanup_old_entries(key, window_seconds)
        current = len(_rate_limits.get(key, []))
        return {
            "limit": limit,
            "remaining": max(0, limit - current),
            "window_seconds": window_seconds,
            "retry_after": window_seconds if current >= limit else 0,
        }


def enforce_rate_limit(request: Request, endpoint: str, limit: int = 60, window_seconds: int = 60) -> None:
    """
    Enforce rate limit for an endpoint.
    Raises HTTPException 429 if exceeded.
    """
    client_ip = request.client.host if request.client else "unknown"
    key = f"{endpoint}:{client_ip}"
    if not check_rate_limit(key, limit, window_seconds):
        info = get_rate_limit_info(key, limit, window_seconds)
        raise HTTPException(
            status_code=429,
            detail={
                "success": False,
                "message": f"Rate limit exceeded. Try again in {info['retry_after']} seconds.",
                "retry_after": info["retry_after"],
            },
        )


# Predefined rate limits
RATE_LIMITS = {
    "login": {"limit": 10, "window": 300},        # 10 per 5 min
    "register": {"limit": 5, "window": 600},      # 5 per 10 min
    "api_general": {"limit": 100, "window": 60},   # 100 per min
    "api_mcp": {"limit": 30, "window": 60},        # 30 per min
    "search": {"limit": 30, "window": 60},         # 30 per min
    "theme": {"limit": 10, "window": 60},          # 10 per min
}


def enforce_predefined_limit(request: Request, limit_name: str) -> None:
    """Enforce a predefined rate limit."""
    config = RATE_LIMITS.get(limit_name, {"limit": 60, "window": 60})
    enforce_rate_limit(request, limit_name, config["limit"], config["window"])
