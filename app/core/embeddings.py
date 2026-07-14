from openai import OpenAI

from app.core.config import get_settings


def get_openai_client() -> OpenAI:
    common = get_settings().common
    if not common.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=common.openai_api_key)


def get_llm_client() -> tuple[OpenAI, str]:
    settings = get_settings()
    api = settings.api
    if api.llm_provider == "llamacpp":
        return (
            OpenAI(base_url=api.llamacpp_base_url, api_key=api.llamacpp_api_key),
            api.llamacpp_chat_model,
        )
    return get_openai_client(), settings.common.openai_chat_model


def get_embedding_client() -> tuple[OpenAI, str]:
    settings = get_settings()
    embed = settings.embed
    if embed.provider == "llamacpp":
        return (
            OpenAI(
                base_url=embed.llamacpp_base_url,
                api_key=embed.llamacpp_api_key,
            ),
            embed.llamacpp_model,
        )
    return get_openai_client(), settings.common.openai_embedding_model


def embed_texts(texts: list[str], *, input_type: str = "document") -> list[list[float]]:
    if not texts:
        return []
    embed = get_settings().embed
    client, model = get_embedding_client()
    prefix = embed.query_prefix if input_type == "query" else embed.document_prefix
    prepared_texts = [f"{prefix}{text}" if prefix else text for text in texts]
    response = client.embeddings.create(model=model, input=prepared_texts)
    return [item.embedding for item in response.data]
