"""
data/cache.py — Simple disk cache for API responses
Prevents hammering the Polygon API during development and scanning.

Usage:
    from data.cache import cache_get, cache_set

    cached = cache_get("AAPL_day")
    if cached is None:
        data = client.get_bars("AAPL", "day")
        cache_set("AAPL_day", data, ttl_seconds=300)
"""

import hashlib
import pickle
import time
import os
from loguru import logger
import config

CACHE_DIR = config.CACHE_DIR
os.makedirs(CACHE_DIR, exist_ok=True)


def _key_to_path(key: str) -> str:
    """Convert a cache key to a safe file path."""
    safe = hashlib.md5(key.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{safe}.pkl")


def cache_get(key: str):
    """
    Retrieve a cached value.
    Returns None if key doesn't exist or has expired.
    """
    path = _key_to_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            entry = pickle.load(f)
        if time.time() > entry["expires_at"]:
            os.remove(path)
            logger.debug(f"Cache expired: {key}")
            return None
        logger.debug(f"Cache hit: {key}")
        return entry["value"]
    except Exception as e:
        logger.warning(f"Cache read error for {key}: {e}")
        return None


def cache_set(key: str, value, ttl_seconds: int = 300):
    """
    Store a value in the cache with a TTL.

    Args:
        key:         Cache key (e.g. "AAPL_day_bars")
        value:       Value to store (any picklable object)
        ttl_seconds: Time to live in seconds (default 5 minutes)
    """
    path = _key_to_path(key)
    try:
        entry = {
            "value": value,
            "expires_at": time.time() + ttl_seconds,
        }
        with open(path, "wb") as f:
            pickle.dump(entry, f)
        logger.debug(f"Cache set: {key} (TTL: {ttl_seconds}s)")
    except Exception as e:
        logger.warning(f"Cache write error for {key}: {e}")


def cache_clear():
    """Clear all cached files. Useful for testing."""
    count = 0
    for f in os.listdir(CACHE_DIR):
        if f.endswith(".pkl"):
            os.remove(os.path.join(CACHE_DIR, f))
            count += 1
    logger.info(f"Cache cleared: {count} entries removed")
