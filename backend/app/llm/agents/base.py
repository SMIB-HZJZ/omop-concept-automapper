"""Pydantic AI models routed through the LiteLLM gateway."""

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider

from app.llm.gateway import chat_model_settings, get_async_client


def gateway_model(model_name: str) -> OpenAIChatModel:
    """Must be called inside the event loop that runs the agent (it reuses that loop's client)."""
    provider = LiteLLMProvider(openai_client=get_async_client())
    settings = chat_model_settings(model_name)
    return OpenAIChatModel(
        model_name,
        provider=provider,
        settings={"temperature": settings["temperature"]} if "temperature" in settings else None,
    )
