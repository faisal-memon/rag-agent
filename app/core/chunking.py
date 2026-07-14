from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import tiktoken

from app.core.config import get_settings

KNOWN_TOKENIZER_MODEL_IDS = {
    "mxbai-embed-large-v1": "mixedbread-ai/mxbai-embed-large-v1",
}


@dataclass
class Chunk:
    chunk_index: int
    content: str
    section: str | None
    page: int | None
    metadata: dict


class TokenizerLike(Protocol):
    def encode(self, text: str) -> list[int]: ...

    def decode(self, token_ids: list[int]) -> str: ...


class TikTokenTokenizer:
    def __init__(self, encoding_name: str) -> None:
        self._encoding = tiktoken.get_encoding(encoding_name)

    def encode(self, text: str) -> list[int]:
        return self._encoding.encode(text)

    def decode(self, token_ids: list[int]) -> str:
        return self._encoding.decode(token_ids)


class HuggingFaceTokenizer:
    def __init__(self, tokenizer) -> None:
        self._tokenizer = tokenizer

    def encode(self, text: str) -> list[int]:
        return self._tokenizer.encode(text, add_special_tokens=False)

    def decode(self, token_ids: list[int]) -> str:
        return self._tokenizer.decode(token_ids, skip_special_tokens=True)


def chunk_text(filename: str, content: str, chunk_size: int, overlap: int) -> list[Chunk]:
    tokenizer = _get_chunk_tokenizer()
    tokens = tokenizer.encode(content)
    chunks: list[Chunk] = []
    start = 0
    chunk_index = 0

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        text = tokenizer.decode(tokens[start:end]).strip()
        if text:
            page = _extract_page_hint(text)
            section = _extract_section_hint(text)
            enriched = f"[Doc: {filename}]\n"
            if section:
                enriched += f"[Section: {section}]\n"
            if page:
                enriched += f"[Page: {page}]\n"
            enriched += f"\n{text}"
            chunks.append(
                Chunk(
                    chunk_index=chunk_index,
                    content=enriched,
                    section=section,
                    page=page,
                    metadata={},
                )
            )
            chunk_index += 1
        if end == len(tokens):
            break
        start = max(end - overlap, start + 1)

    return chunks


@lru_cache(maxsize=1)
def _get_chunk_tokenizer() -> TokenizerLike:
    embed = get_settings().embed
    model_id = KNOWN_TOKENIZER_MODEL_IDS.get(
        embed.tokenizer_model_id,
        embed.tokenizer_model_id,
    )

    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            model_id,
            local_files_only=embed.tokenizer_local_files_only,
        )
        print(f"[chunking] Using embedding tokenizer model_id={model_id}", flush=True)
        return HuggingFaceTokenizer(tokenizer)
    except Exception as exc:
        print(
            f"[chunking] Falling back to tiktoken model_id={model_id} reason={exc}",
            flush=True,
        )
        return TikTokenTokenizer("cl100k_base")


def _extract_page_hint(text: str) -> int | None:
    for line in text.splitlines():
        if line.startswith("[Page:") and line.endswith("]"):
            try:
                return int(line.removeprefix("[Page:").removesuffix("]").strip())
            except ValueError:
                return None
    return None


def _extract_section_hint(text: str) -> str | None:
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("[Page:"):
            continue
        if len(candidate) <= 80 and candidate == candidate.title():
            return candidate
    return None
