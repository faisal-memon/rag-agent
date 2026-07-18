"""Chat-model client factories shared by answer-generation paths."""

from openai import OpenAI

from app.core.config import get_settings


def get_openai_client() -> OpenAI:
    """Create the configured OpenAI client for hosted chat or embeddings."""
    common = get_settings().common
    if not common.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=common.openai_api_key)


def get_llm_client() -> tuple[OpenAI, str]:
    """Create the configured chat-completions client and select its model."""
    settings = get_settings()
    api = settings.api
    if api.llm_provider == "llamacpp":
        return (
            OpenAI(base_url=api.llamacpp_base_url, api_key=api.llamacpp_api_key),
            api.llamacpp_chat_model,
        )
    return get_openai_client(), settings.common.openai_chat_model
