"""Chat-model client construction for the API container."""

from openai import OpenAI

from app.agent.config import ApiSettings
from app.core.embeddings import get_openai_client


def get_llm_client(settings: ApiSettings) -> tuple[OpenAI, str]:
    if settings.llm_provider == "llamacpp":
        return OpenAI(base_url=settings.llamacpp_base_url, api_key=settings.llamacpp_api_key), settings.llamacpp_chat_model
    return get_openai_client(settings.openai_api_key), settings.openai_chat_model
