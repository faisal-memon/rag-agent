import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.api.config import get_api_settings
from app.api.search import search_debug
from app.api.schemas import QueryRequest


class SearchTest(unittest.TestCase):
    def test_keyword_search_does_not_generate_an_embedding(self) -> None:
        with (
            patch("app.api.search._keyword_rows", return_value=[]) as keyword_rows,
            patch("app.api.search.embed_texts") as embed_texts,
        ):
            result = search_debug("adjusted gross income", mode="keyword")

        keyword_rows.assert_called_once_with("adjusted gross income", 8, 0, get_api_settings())
        embed_texts.assert_not_called()
        self.assertEqual([], result["chunks"])

    def test_semantic_search_generates_query_embedding(self) -> None:
        with (
            patch("app.api.search._semantic_rows", return_value=[]) as semantic_rows,
            patch("app.api.search.embed_texts", return_value=[[0.1, 0.2]]) as embed_texts,
        ):
            search_debug("taxable income concept", mode="semantic")

        settings = get_api_settings()
        embed_texts.assert_called_once_with(
            ["taxable income concept"], provider=settings.embedding_provider,
            llamacpp_base_url=settings.embedding_llamacpp_base_url,
            llamacpp_api_key=settings.embedding_llamacpp_api_key,
            llamacpp_model=settings.embedding_llamacpp_model,
            openai_api_key=settings.openai_api_key,
            openai_embedding_model=settings.openai_embedding_model,
            query_prefix=settings.embedding_query_prefix,
            document_prefix=settings.embedding_document_prefix,
            input_type="query",
        )
        semantic_rows.assert_called_once_with([0.1, 0.2], 8, 0, settings)

    def test_search_schema_rejects_unsupported_auto_mode(self) -> None:
        with self.assertRaises(ValidationError):
            QueryRequest(question="test", mode="auto")


if __name__ == "__main__":
    unittest.main()
