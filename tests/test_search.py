import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.agent.config import get_api_settings
from app.agent.search import search_debug
from app.agent.tools import hybrid_search
from app.agent.api.schemas import QueryRequest


class SearchTest(unittest.TestCase):
    def test_hybrid_search_merges_exact_and_semantic_candidates(self) -> None:
        keyword_chunk = {
            "chunk_id": 1,
            "filename": "tax.md",
            "content": "adjusted gross income",
            "matched_fts": True,
            "matched_vector": False,
            "fts_score": 0.8,
            "vector_score": 0.0,
        }
        semantic_chunk = {
            "chunk_id": 2,
            "filename": "income.md",
            "content": "taxable income concept",
            "matched_fts": False,
            "matched_vector": True,
            "fts_score": 0.0,
            "vector_score": 0.9,
        }
        with (
            patch("app.agent.tools.keyword_search", return_value=[keyword_chunk]),
            patch("app.agent.tools.semantic_search", return_value=[semantic_chunk]),
        ):
            result = hybrid_search("income")

        self.assertEqual([1, 2], [chunk["chunk_id"] for chunk in result])
        self.assertTrue(all(chunk["retrieval_mode"] == "hybrid" for chunk in result))

    def test_keyword_search_does_not_generate_an_embedding(self) -> None:
        with (
            patch("app.agent.search._keyword_rows", return_value=[]) as keyword_rows,
            patch("app.agent.search.embed_texts") as embed_texts,
        ):
            result = search_debug("adjusted gross income", mode="keyword")

        keyword_rows.assert_called_once_with("adjusted gross income", 8, 0, get_api_settings())
        embed_texts.assert_not_called()
        self.assertEqual([], result["chunks"])

    def test_semantic_search_generates_query_embedding(self) -> None:
        with (
            patch("app.agent.search._semantic_rows", return_value=[]) as semantic_rows,
            patch("app.agent.search.embed_texts", return_value=[[0.1, 0.2]]) as embed_texts,
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
