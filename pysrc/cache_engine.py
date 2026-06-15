"""
Cache Engine — Redis-powered caching and real-time collaboration layer.

Redis capabilities activated:
  - Graph query cache: cache Neo4j traversal results (TTL 5 min)
  - Session pub/sub: real-time multi-user event broadcasting
  - Rate limiting: per-session, per-tool rate limiting
  - Active presence: track which users are active in which sessions
  - Decision notifications: when user A decides, notify user B
  - Query result cache: cache expensive pgvector similarity searches
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class CacheEngine:
    """Redis-backed caching and real-time collaboration."""

    def __init__(self, redis_client=None, max_size: int = 1024, default_ttl: int = 300):
        self._redis = redis_client
        self._pubsub: Optional[asyncio.Task] = None
        self._handlers: dict[str, list] = {}
        self._local_cache: dict[str, tuple[Any, float]] = {}
        self._max_size = max_size
        self._default_ttl = default_ttl
        self.hits = 0
        self.misses = 0

    def set(self, key: str, value: Any, ttl: int = None) -> None:
        if len(self._local_cache) >= self._max_size and key not in self._local_cache:
            oldest = min(self._local_cache.items(), key=lambda item: item[1][1])[0]
            self._local_cache.pop(oldest, None)
        expires_at = time.time() + (ttl if ttl is not None else self._default_ttl)
        self._local_cache[key] = (value, expires_at)

    def get(self, key: str) -> Any:
        item = self._local_cache.get(key)
        if item is None:
            self.misses += 1
            return None
        value, expires_at = item
        if expires_at < time.time():
            self._local_cache.pop(key, None)
            self.misses += 1
            return None
        self.hits += 1
        return value

    def delete(self, key: str) -> bool:
        return self._local_cache.pop(key, None) is not None

    def clear(self) -> None:
        self._local_cache.clear()

    def get_or_set(self, key: str, factory, ttl: int = None) -> Any:
        value = self.get(key)
        if value is not None:
            return value
        value = factory()
        self.set(key, value, ttl)
        return value

    # ── Graph query cache ──────────────────────────────

    async def cache_graph_query(self, query_type: str, params: dict, result: any, ttl: int = 300):
        """Cache a graph traversal result."""
        key = _cache_key("graph", query_type, params)
        try:
            await self._redis.setex(key, ttl, json.dumps(result, ensure_ascii=False, default=str))
        except Exception as e:
            logger.debug(f"Cache write failed: {e}")

    async def get_cached_graph_query(self, query_type: str, params: dict) -> Optional[any]:
        """Retrieve a cached graph traversal result."""
        key = _cache_key("graph", query_type, params)
        try:
            raw = await self._redis.get(key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None

    # ── Vector search cache ────────────────────────────

    async def cache_vector_search(self, query_hash: str, results: list, ttl: int = 600):
        """Cache pgvector similarity search results."""
        key = f"vec:cache:{query_hash}"
        try:
            await self._redis.setex(key, ttl, json.dumps(results, ensure_ascii=False, default=str))
        except Exception:
            pass

    async def get_cached_vector_search(self, query_hash: str) -> Optional[list]:
        """Retrieve cached vector search results."""
        try:
            raw = await self._redis.get(f"vec:cache:{query_hash}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None

    # ── Session presence (multi-user) ──────────────────

    async def user_joined(self, session_id: str, user_id: str, user_name: str = ""):
        """Record a user joining a session."""
        try:
            key = f"presence:{session_id}"
            await self._redis.hset(key, user_id, json.dumps({
                "user_name": user_name or user_id,
                "joined_at": time.time(),
                "last_seen": time.time(),
            }))
            await self._redis.expire(key, 86400)
            # Notify other users
            await self._publish(session_id, {
                "type": "user_joined",
                "user_id": user_id,
                "user_name": user_name or user_id,
                "timestamp": time.time(),
            })
        except Exception as e:
            logger.debug(f"Presence write failed: {e}")

    async def user_left(self, session_id: str, user_id: str):
        """Record a user leaving a session."""
        try:
            await self._redis.hdel(f"presence:{session_id}", user_id)
            await self._publish(session_id, {
                "type": "user_left",
                "user_id": user_id,
                "timestamp": time.time(),
            })
        except Exception as e:
            logger.debug(f"Presence delete failed: {e}")

    async def get_active_users(self, session_id: str) -> list[dict]:
        """Get list of active users in a session."""
        try:
            raw = await self._redis.hgetall(f"presence:{session_id}")
            users = []
            now = time.time()
            for uid, data in raw.items():
                info = json.loads(data) if isinstance(data, (str, bytes)) else data
                if isinstance(info, dict):
                    last_seen = info.get("last_seen", 0)
                    if now - last_seen < 300:  # Active in last 5 minutes
                        users.append({"user_id": uid, "user_name": info.get("user_name", uid)})
            return users
        except Exception:
            return []

    async def heartbeat(self, session_id: str, user_id: str):
        """Update last_seen timestamp."""
        try:
            key = f"presence:{session_id}"
            raw = await self._redis.hget(key, user_id)
            if raw:
                info = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
                if isinstance(info, dict):
                    info["last_seen"] = time.time()
                    await self._redis.hset(key, user_id, json.dumps(info))
        except Exception:
            pass

    # ── Decision notifications ─────────────────────────

    async def notify_decision(self, session_id: str, decision_id: str,
                               topic: str, chosen: str, owner_name: str):
        """Broadcast a decision to all users in a session."""
        try:
            await self._publish(session_id, {
                "type": "decision_made",
                "decision_id": decision_id,
                "topic": topic,
                "chosen": chosen,
                "owner_name": owner_name,
                "timestamp": time.time(),
                "message": f"{owner_name} 选择了「{chosen}」作为 {topic} 的方案",
            })
        except Exception as e:
            logger.debug(f"Decision notify failed: {e}")

    # ── Pub/Sub infrastructure ─────────────────────────

    async def _publish(self, session_id: str, event: dict):
        """Publish an event to a session channel."""
        try:
            channel = f"session:{session_id}"
            await self._redis.publish(channel, json.dumps(event, ensure_ascii=False))
        except Exception:
            pass

    async def subscribe(self, session_id: str, handler):
        """Subscribe to session events. Handler receives event dicts."""
        try:
            channel = f"session:{session_id}"
            if channel not in self._handlers:
                self._handlers[channel] = []
            self._handlers[channel].append(handler)

            if self._pubsub is None or self._pubsub.done():
                self._pubsub = asyncio.create_task(self._listen_loop())
        except Exception as e:
            logger.warning(f"Subscribe failed: {e}")

    async def unsubscribe(self, session_id: str, handler):
        """Unsubscribe from session events."""
        channel = f"session:{session_id}"
        if channel in self._handlers:
            self._handlers[channel] = [h for h in self._handlers[channel] if h is not handler]

    async def _listen_loop(self):
        """Background task that listens for pub/sub messages."""
        try:
            pubsub = self._redis.pubsub()
            channels = list(self._handlers.keys())
            if channels:
                await pubsub.subscribe(*channels)
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        channel = message["channel"].decode() if isinstance(message["channel"], bytes) else message["channel"]
                        data_raw = message["data"]
                        try:
                            event = json.loads(data_raw) if isinstance(data_raw, (str, bytes)) else data_raw
                        except Exception:
                            continue
                        for handler in self._handlers.get(channel, []):
                            try:
                                if asyncio.iscoroutinefunction(handler):
                                    asyncio.create_task(handler(event))
                                else:
                                    handler(event)
                            except Exception:
                                pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"PubSub listen loop error: {e}")

    # ── Rate limiting ──────────────────────────────────

    async def check_rate_limit(self, key: str, max_calls: int = 10, window_sec: int = 60) -> bool:
        """Check if a rate limit is exceeded. Returns True if allowed."""
        redis_key = f"ratelimit:{key}"
        try:
            current = await self._redis.get(redis_key)
            count = int(current) if current else 0
            if count >= max_calls:
                return False
            pipe = self._redis.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_sec)
            await pipe.execute()
            return True
        except Exception:
            return True  # Fail open if Redis is down

    # ── Shared context ─────────────────────────────────

    async def set_shared_context(self, session_id: str, key: str, value: any, ttl: int = 3600):
        """Store shared context visible to all users in a session."""
        redis_key = f"ctx:{session_id}:{key}"
        try:
            await self._redis.setex(redis_key, ttl, json.dumps(value, ensure_ascii=False, default=str))
        except Exception:
            pass

    async def get_shared_context(self, session_id: str, key: str) -> Optional[any]:
        """Get shared context."""
        try:
            raw = await self._redis.get(f"ctx:{session_id}:{key}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None

    async def get_all_shared_context(self, session_id: str) -> dict:
        """Get all shared context keys for a session."""
        try:
            keys = await self._redis.keys(f"ctx:{session_id}:*")
            result = {}
            for k in keys:
                k_str = k.decode() if isinstance(k, bytes) else k
                raw = await self._redis.get(k_str)
                if raw:
                    key_name = k_str.split(":", 2)[-1] if ":" in k_str else k_str
                    try:
                        result[key_name] = json.loads(raw)
                    except Exception:
                        result[key_name] = raw
            return result
        except Exception:
            return {}


def _cache_key(prefix: str, query_type: str, params: dict) -> str:
    """Generate a stable cache key."""
    payload = json.dumps({"type": query_type, "params": params}, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{prefix}:{query_type}:{digest}"
