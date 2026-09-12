"""In-memory cache with TTL support."""
import time
from collections import OrderedDict
from threading import Lock
from typing import Any, Optional


class TTLCache:
    """Thread-safe in-memory cache with TTL."""

    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        self._cache: OrderedDict[str, tuple] = OrderedDict()
        self._lock = Lock()
        self._max_size = max_size
        self._default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache. Returns None if expired or missing."""
        with self._lock:
            if key not in self._cache:
                return None
            value, expiry = self._cache[key]
            if time.time() > expiry:
                del self._cache[key]
                return None
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache with TTL."""
        ttl = ttl or self._default_ttl
        expiry = time.time() + ttl
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, expiry)
            # Evict oldest if over max size
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()

    def cleanup(self) -> int:
        """Remove expired entries. Returns count removed."""
        now = time.time()
        removed = 0
        with self._lock:
            expired = [k for k, (_, exp) in self._cache.items() if now > exp]
            for k in expired:
                del self._cache[k]
                removed += 1
        return removed

    @property
    def size(self) -> int:
        return len(self._cache)


# Global cache instances
material_cache = TTLCache(max_size=500, default_ttl=300)    # 5 min TTL
user_cache = TTLCache(max_size=200, default_ttl=60)         # 1 min TTL
search_cache = TTLCache(max_size=100, default_ttl=120)      # 2 min TTL
api_cache = TTLCache(max_size=50, default_ttl=60)           # 1 min TTL
