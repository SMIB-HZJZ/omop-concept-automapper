"""Embedding client (through the gateway) with retries and a Redis cache for query vectors."""

import hashlib
import logging
from collections.abc import Sequence

import numpy as np
import openai
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.redis import get_redis_bytes
from app.llm.gateway import get_async_client, get_sync_client

logger = logging.getLogger(__name__)


class GatewayUnavailable(RuntimeError):
    pass


class EmptyEmbeddingResponse(RuntimeError):
    pass


# Worth retrying: network trouble, timeouts, rate limits, 5xx, and the provider returning nothing.
# A 400/401/404 (bad model name, wrong key, too-long input) fails the same way every time.
RETRYABLE_ERRORS = (
    openai.APIConnectionError,  # includes APITimeoutError
    openai.RateLimitError,
    openai.InternalServerError,
    EmptyEmbeddingResponse,
)


def _unreachable(exc: Exception) -> GatewayUnavailable:
    url = get_settings().llm_gateway_url
    return GatewayUnavailable(
        f"Cannot reach the LLM gateway at {url}: {exc}. Is the litellm service running?"
    )


class Embedder:
    def __init__(self, model: str, dims: int | None):
        self.model = model
        self.dims = dims
        retries = get_settings().embedding_max_retries
        self._retry = retry(
            stop=stop_after_attempt(retries),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type(RETRYABLE_ERRORS),
            reraise=True,
        )

    def _params(self, texts: Sequence[str]) -> dict:
        params: dict = {"model": self.model, "input": list(texts)}
        if self.dims:
            params["dimensions"] = self.dims
        return params

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        @self._retry
        def call():
            response = get_sync_client().embeddings.create(**self._params(texts))
            if not response.data:
                raise EmptyEmbeddingResponse("The embedding provider returned no vectors")
            return [d.embedding for d in sorted(response.data, key=lambda d: d.index)]

        try:
            return call()
        except openai.APIConnectionError as exc:
            raise _unreachable(exc) from exc

    async def aembed(self, texts: Sequence[str]) -> list[list[float]]:
        @self._retry
        async def call():
            response = await get_async_client().embeddings.create(**self._params(texts))
            if not response.data:
                raise EmptyEmbeddingResponse("The embedding provider returned no vectors")
            return [d.embedding for d in sorted(response.data, key=lambda d: d.index)]

        try:
            return await call()
        except openai.APIConnectionError as exc:
            raise _unreachable(exc) from exc

    def _cache_key(self, text: str) -> str:
        digest = hashlib.sha1(text.strip().lower().encode()).hexdigest()
        return f"emb:{self.model}:{self.dims}:{digest}"

    def embed_query(self, text: str) -> list[float]:
        key = self._cache_key(text)
        try:
            hit = get_redis_bytes().get(key)
            if hit:
                return np.frombuffer(hit, dtype=np.float32).tolist()
        except Exception:
            logger.debug("Query embedding cache read failed", exc_info=True)
        vector = self.embed([text])[0]
        self._store(key, vector)
        return vector

    async def aembed_query(self, text: str) -> list[float]:
        key = self._cache_key(text)
        try:
            hit = get_redis_bytes().get(key)
            if hit:
                return np.frombuffer(hit, dtype=np.float32).tolist()
        except Exception:
            logger.debug("Query embedding cache read failed", exc_info=True)
        vector = (await self.aembed([text]))[0]
        self._store(key, vector)
        return vector

    def _store(self, key: str, vector: list[float]) -> None:
        try:
            get_redis_bytes().set(
                key,
                np.asarray(vector, dtype=np.float32).tobytes(),
                ex=get_settings().query_embedding_cache_ttl,
            )
        except Exception:
            logger.debug("Query embedding cache write failed", exc_info=True)

    def probe_dims(self) -> int:
        """Actual vector size returned by the model (some providers ignore ``dimensions``)."""
        return len(self.embed(["test"])[0])
