"""Lightweight in-memory TTL cache used for short-lived agent results.

Minimal, thread-safe, zero-dependency cache. Keys must be hashable.
Provides get_or_set and decorator usage. Keep behavior simple to avoid
global-state pollution or surprising persistence.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class TTLCache:
    def __init__(self, default_ttl: int = 300):
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.default_ttl = default_ttl

    def get(self, key: Any):
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            expire_at, value = entry
            if expire_at is not None and expire_at < now:
                # expired
                del self._data[key]
                return None
            return value

    def set(self, key: Any, value: Any, ttl: int | None = None) -> None:
        expire_at = None if ttl is None else time.time() + (ttl or self.default_ttl)
        with self._lock:
            self._data[key] = (expire_at, value)

    def get_or_set(self, key: Any, factory: Callable[[], Any], ttl: int | None = None) -> Any:
        val = self.get(key)
        if val is not None:
            return val
        value = factory()
        self.set(key, value, ttl=ttl)
        return value

    def invalidate(self, key: Any) -> None:
        with self._lock:
            self._data.pop(key, None)


def ttl_cached(default_ttl: int = 300):
    cache = TTLCache(default_ttl=default_ttl)

    def decorator(fn: Callable):
        def wrapper(*args, **kwargs):
            key = (fn.__module__, fn.__name__, args, tuple(sorted(kwargs.items())))
            return cache.get_or_set(key, lambda: fn(*args, **kwargs))
        return wrapper
    return decorator
