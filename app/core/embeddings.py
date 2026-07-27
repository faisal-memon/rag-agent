from openai import OpenAI

def get_openai_client(api_key: str) -> OpenAI:
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=api_key)


def get_embedding_client(
    *, provider: str, llamacpp_base_url: str, llamacpp_api_key: str, llamacpp_model: str,
    openai_api_key: str, openai_embedding_model: str,
) -> tuple[OpenAI, str]:
    if provider == "llamacpp":
        return (
            OpenAI(
                base_url=llamacpp_base_url,
                api_key=llamacpp_api_key,
            ),
            llamacpp_model,
        )
    return get_openai_client(openai_api_key), openai_embedding_model


def embed_texts(
    texts: list[str], *, provider: str, llamacpp_base_url: str, llamacpp_api_key: str,
    llamacpp_model: str, openai_api_key: str, openai_embedding_model: str,
    query_prefix: str = "", document_prefix: str = "", input_type: str = "document",
) -> list[list[float]]:
    if not texts:
        return []
    client, model = get_embedding_client(
        provider=provider, llamacpp_base_url=llamacpp_base_url, llamacpp_api_key=llamacpp_api_key,
        llamacpp_model=llamacpp_model, openai_api_key=openai_api_key,
        openai_embedding_model=openai_embedding_model,
    )
    prefix = query_prefix if input_type == "query" else document_prefix
    prepared_texts = [f"{prefix}{text}" if prefix else text for text in texts]
    response = client.embeddings.create(model=model, input=prepared_texts)
    return [item.embedding for item in response.data]
