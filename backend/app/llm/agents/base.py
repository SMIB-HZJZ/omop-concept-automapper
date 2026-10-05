"""Pydantic AI models routed through the LiteLLM gateway."""

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider

from app.core.config import get_settings


def gateway_model(model_name: str) -> OpenAIChatModel:
    settings = get_settings()
    provider = LiteLLMProvider(
        api_base=settings.llm_gateway_url.rstrip("/") + "/v1",
        api_key=settings.litellm_master_key,
    )
    return OpenAIChatModel(model_name, provider=provider)
