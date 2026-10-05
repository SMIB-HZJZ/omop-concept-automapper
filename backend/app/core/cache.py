"""Small Redis-backed JSON cache with namespace invalidation.

Keys are grouped in namespaces (e.g. ``vocab``, ``mapping:3``). Invalidating a namespace
bumps its version counter, so stale keys simply stop being read and expire on their own.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from fastapi.encoders import jsonable_encoder

from app.core.config import get_settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _ns_version(namespace: str) -> str:
    try:
        return get_redis().get(f"cache:ns:{namespace}") or "0"
    except Exception:  # Redis down: behave as a cache miss
        return "x"


def invalidate(*namespaces: str) -> None:
    for namespace in namespaces:
        try:
            get_redis().incr(f"cache:ns:{namespace}")
        except Exception:
            logger.warning("Cache invalidation failed for %s", namespace, exc_info=True)


def cached(namespace: str | Callable[..., str], ttl: int | None = None):
    """Cache a function's JSON-serialisable result.

    ``namespace`` may be a callable receiving the function's arguments, so a namespace can
    depend on e.g. a vocabulary id.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            ns = namespace(*args, **kwargs) if callable(namespace) else namespace
            version = _ns_version(ns)
            if version == "x":
                return fn(*args, **kwargs)
            arg_key = hashlib.sha1(
                json.dumps(
                    [_key_part(a) for a in args] + [sorted(kwargs.items())], default=str
                ).encode()
            ).hexdigest()
            key = f"cache:{ns}:{version}:{fn.__module__}.{fn.__qualname__}:{arg_key}"
            try:
                hit = get_redis().get(key)
                if hit is not None:
                    return json.loads(hit)
            except Exception:
                return fn(*args, **kwargs)
            value = fn(*args, **kwargs)
            try:
                get_redis().set(
                    key,
                    json.dumps(jsonable_encoder(value)),
                    ex=ttl or get_settings().cache_ttl_seconds,
                )
            except Exception:
                logger.warning("Cache write failed for %s", key, exc_info=True)
            return jsonable_encoder(value)

        return wrapper

    return decorator


def _key_part(arg: Any) -> Any:
    # Sessions and other objects are not part of the cache key
    if isinstance(arg, (str, int, float, bool, type(None), list, tuple, dict)):
        return arg
    return None
