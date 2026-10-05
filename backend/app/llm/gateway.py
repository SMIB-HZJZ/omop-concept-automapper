"""The single entry point to LLMs: an OpenAI-compatible client pointed at the LiteLLM proxy.

Every provider (OpenAI, Anthropic, Azure, Bedrock, Ollama, …) is reached through the proxy,
so switching provider only means changing model names / keys in the environment.
"""

import logging
from functools import lru_cache

import httpx
from openai import AsyncOpenAI, OpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _base_url() -> str:
    return get_settings().llm_gateway_url.rstrip("/") + "/v1"


@lru_cache
def get_async_client() -> AsyncOpenAI:
    s = get_settings()
    return AsyncOpenAI(
        base_url=_base_url(),
        api_key=s.litellm_master_key,
        timeout=s.llm_timeout_seconds,
        max_retries=0,
    )


@lru_cache
def get_sync_client() -> OpenAI:
    s = get_settings()
    return OpenAI(
        base_url=_base_url(),
        api_key=s.litellm_master_key,
        timeout=s.llm_timeout_seconds,
        max_retries=0,
    )


def list_gateway_models() -> list[str] | None:
    """Models the proxy reports, or None if it is unreachable."""
    try:
        return [m.id for m in get_sync_client().models.list().data]
    except Exception as exc:
        logger.info("Could not list gateway models: %s", exc)
        return None


def gateway_healthy() -> bool:
    s = get_settings()
    try:
        resp = httpx.get(
            s.llm_gateway_url.rstrip("/") + "/health/liveliness",
            timeout=3,
        )
        return resp.status_code == 200
    except Exception:
        return False


def chat_model_settings() -> dict:
    """Extra parameters for chat calls. Temperature is only sent when configured, because
    some models (e.g. reasoning models) reject a non-default temperature."""
    s = get_settings()
    return {} if s.llm_temperature is None else {"temperature": s.llm_temperature}
