import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.agent.config import get_api_settings
from app.agent.search import search_debug
from app.agent.tools import hybrid_search
from app.agent.api.schemas import QueryRequest


class SearchTest(unittest.TestCase):
    def test_hybrid_search_preserves_ordered_search_lists(self) -> None:
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
            patch("app.agent.tools.builtin.keyword_search", return_value=[keyword_chunk]),
            patch("app.agent.tools.builtin.semantic_search", return_value=[semantic_chunk]),
        ):
            result = hybrid_search("income")

        self.assertEqual([keyword_chunk], result["keyword_matches"])
        self.assertEqual([semantic_chunk], result["semantic_matches"])

    def test_keyword_search_does_not_generate_an_embedding(self) -> None:
        with (
            patch("app.agent.search._keyword_rows", return_value=[]) as keyword_rows,
            patch("app.agent.search.embed_texts") as embed_texts,
        ):
            result = search_debug("adjusted gross income", mode="keyword")

        keyword_rows.assert_called_once_with("adjusted gross income", 8, 0, get_api_settings())
        embed_texts.assert_not_called()
        self.assertEqual([], result["chunks"])

    def test_semantic_search_uses_shared_embedding_settings(self) -> None:
        embedding_settings = SimpleNamespace(
            provider="llamacpp",
            llamacpp_base_url="http://embed.local/v1",
            llamacpp_api_key="not-needed",
            llamacpp_model="mxbai",
            openai_api_key="",
            openai_embedding_model="unused",
            query_prefix="query: ",
            document_prefix="document: ",
        )
        with (
            patch("app.agent.search._semantic_rows", return_value=[]) as semantic_rows,
            patch("app.agent.search.embed_texts", return_value=[[0.1, 0.2]]) as embed_texts,
            patch("app.agent.search.get_embed_settings", return_value=embedding_settings),
        ):
            search_debug("taxable income concept", mode="semantic")

        api_settings = get_api_settings()
        embed_texts.assert_called_once_with(
            ["taxable income concept"],
            provider="llamacpp",
            llamacpp_base_url="http://embed.local/v1",
            llamacpp_api_key="not-needed",
            llamacpp_model="mxbai",
            openai_api_key="",
            openai_embedding_model="unused",
            query_prefix="query: ",
            document_prefix="document: ",
            input_type="query",
        )
        semantic_rows.assert_called_once_with([0.1, 0.2], 8, 0, api_settings)

    def test_search_schema_rejects_unsupported_auto_mode(self) -> None:
        with self.assertRaises(ValidationError):
            QueryRequest(question="test", mode="auto")


if __name__ == "__main__":
    unittest.main()
