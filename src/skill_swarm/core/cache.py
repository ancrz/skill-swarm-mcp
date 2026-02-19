"""TTL file-based cache for search results and trust scores."""

import hashlib
import json
import logging
import time
from pathlib import Path

from skill_swarm.config import settings

logger = logging.getLogger("skill-swarm.cache")


def _cache_key(prefix: str, *parts: str) -> str:
    """Generate a deterministic cache filename."""
    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{prefix}_{digest}.json"


def get_cached(prefix: str, *key_parts: str, ttl: int | None = None) -> dict | list | None:
    """Read from cache if entry exists and is within TTL.

    Returns None on miss (expired or not found).
    """
    if ttl is None:
        ttl = settings.cache_search_ttl

    cache_dir = settings.cache_path
    if not cache_dir.exists():
        return None

    filename = _cache_key(prefix, *key_parts)
    cache_file = cache_dir / filename

    if not cache_file.exists():
        return None

    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        stored_at = data.get("_stored_at", 0)

        if time.time() - stored_at > ttl:
            cache_file.unlink(missing_ok=True)
            logger.debug("Cache expired: %s", filename)
            return None

        logger.debug("Cache hit: %s", filename)
        return data.get("payload")
    except Exception as e:
        logger.warning("Cache read error: %s", e)
        return None


def set_cached(prefix: str, *key_parts: str, payload: dict | list) -> None:
    """Write payload to cache with timestamp."""
    cache_dir = settings.cache_path
    cache_dir.mkdir(parents=True, exist_ok=True)

    filename = _cache_key(prefix, *key_parts)
    cache_file = cache_dir / filename

    try:
        cache_file.write_text(
            json.dumps({"_stored_at": time.time(), "payload": payload}, default=str),
            encoding="utf-8",
        )
        logger.debug("Cache set: %s", filename)
    except Exception as e:
        logger.warning("Cache write error: %s", e)


def purge_prefix(prefix: str) -> int:
    """Remove all cache entries with a given prefix. Returns count removed."""
    cache_dir = settings.cache_path
    if not cache_dir.exists():
        return 0

    removed = 0
    for f in cache_dir.glob(f"{prefix}_*.json"):
        f.unlink(missing_ok=True)
        removed += 1

    if removed:
        logger.info("Purged %d cache entries with prefix '%s'", removed, prefix)
    return removed


def purge_all() -> int:
    """Remove all cache entries."""
    cache_dir = settings.cache_path
    if not cache_dir.exists():
        return 0

    removed = 0
    for f in cache_dir.glob("*.json"):
        f.unlink(missing_ok=True)
        removed += 1
    return removed


def cache_stats() -> dict:
    """Return cache directory stats."""
    cache_dir = settings.cache_path
    if not cache_dir.exists():
        return {"entries": 0, "size_bytes": 0}

    files = list(cache_dir.glob("*.json"))
    total_size = sum(f.stat().st_size for f in files)
    return {
        "entries": len(files),
        "size_bytes": total_size,
        "path": str(cache_dir),
    }
