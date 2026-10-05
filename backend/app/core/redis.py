"""Shared Redis client."""

from functools import lru_cache

import redis

from app.core.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


@lru_cache
def get_redis_bytes() -> redis.Redis:
    """Client without response decoding, for binary payloads such as embeddings."""
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=False)
