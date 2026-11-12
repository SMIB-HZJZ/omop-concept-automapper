"""The single entry point to LLMs: an OpenAI-compatible client pointed at the LiteLLM proxy.

Every provider (OpenAI, Anthropic, Azure, Bedrock, Ollama, …) is reached through the proxy,
so switching provider only means changing model names / keys in the environment.
"""

import asyncio
import logging
import weakref
from functools import lru_cache

import httpx
from openai import AsyncOpenAI, OpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _base_url() -> str:
    return get_settings().llm_gateway_url.rstrip("/") + "/v1"


# One async client per event loop. httpx connections belong to the loop that opened them, and every
# Celery task runs its own ``asyncio.run`` loop: a process-wide client would hand the second task
# connections of a closed loop ("Event loop is closed").
_async_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncOpenAI]" = (
    weakref.WeakKeyDictionary()
)


def get_async_client() -> AsyncOpenAI:
    """The gateway client for the running event loop (created on first use in that loop)."""
    loop = asyncio.get_running_loop()
    client = _async_clients.get(loop)
    if client is None:
        s = get_settings()
        client = AsyncOpenAI(
            base_url=_base_url(),
            api_key=s.litellm_master_key,
            timeout=s.llm_timeout_seconds,
            max_retries=0,
        )
        _async_clients[loop] = client
    return client


async def aclose_async_client() -> None:
    """Close the running loop's client; call before the loop ends (e.g. at the end of a task)."""
    client = _async_clients.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.close()


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
    """Extra parameters for chat calls.

    Temperature defaults to 0 so mapping is as repeatable as the model allows. Reasoning models
    (e.g. gpt-5, o-series) reject it; the LiteLLM proxy runs with ``drop_params: true`` and drops it
    for them. Set LLM_TEMPERATURE to an empty value to never send it."""
    s = get_settings()
    return {} if s.llm_temperature is None else {"temperature": s.llm_temperature}
