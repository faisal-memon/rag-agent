import os
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.core.config import get_settings


class ConfigTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("EMBED_RECONCILE_INTERVAL_SECONDS", None)
        os.environ.pop("NORMALIZE_RECONCILE_INTERVAL_SECONDS", None)
        os.environ.pop("NORMALIZE_WATCH_STABILITY_CHECKS", None)
        os.environ.pop("POSTGRES_HOST", None)
        os.environ.pop("RAG_AGENT_MAX_STEPS", None)
        os.environ.pop("RAG_ENABLED_SUFFIXES", None)
        os.environ.pop("RAG_MEMORY_PATH", None)
        get_settings.cache_clear()

    def test_stability_checks_must_be_positive(self) -> None:
        os.environ["NORMALIZE_WATCH_STABILITY_CHECKS"] = "0"
        get_settings.cache_clear()

        with self.assertRaises(ValidationError):
            get_settings()

    def test_settings_exposes_grouped_views(self) -> None:
        settings = get_settings()

        self.assertEqual("rag", settings.common.database.db)
        self.assertEqual(Path("/data/nextcloud"), settings.common.nextcloud_source_dir)
        self.assertEqual(6, settings.api.agent_max_steps)
        self.assertEqual(Path("/memory/MEMORY.md"), settings.api.memory_path)
        self.assertEqual(500, settings.embed.chunk_size)
        self.assertEqual("mixedbread-ai/mxbai-embed-large-v1", settings.embed.tokenizer_model_id)
        self.assertEqual("docling", settings.normalize.backend)
        self.assertEqual(
            {".pdf", ".docx", ".txt", ".md", ".jpg", ".jpeg", ".png"},
            settings.normalize.enabled_suffixes,
        )

    def test_grouped_settings_follow_environment_overrides(self) -> None:
        os.environ["RAG_AGENT_MAX_STEPS"] = "4"
        os.environ["NORMALIZE_WATCH_STABILITY_CHECKS"] = "2"
        os.environ["POSTGRES_HOST"] = "postgres"
        os.environ["RAG_MEMORY_PATH"] = "/tmp/rag-memory.md"
        get_settings.cache_clear()

        settings = get_settings()

        self.assertEqual(4, settings.api.agent_max_steps)
        self.assertEqual(2, settings.normalize.stability_checks)
        self.assertEqual("postgres", settings.common.database.host)
        self.assertEqual(Path("/tmp/rag-memory.md"), settings.api.memory_path)

    def test_enabled_suffixes_accept_comma_separated_env_value(self) -> None:
        os.environ["RAG_ENABLED_SUFFIXES"] = ".pdf,docx, TXT , .jpg"
        get_settings.cache_clear()

        settings = get_settings()

        self.assertEqual({".pdf", ".docx", ".txt", ".jpg"}, settings.normalize.enabled_suffixes)

    def test_reconcile_interval_must_not_be_negative(self) -> None:
        os.environ["NORMALIZE_RECONCILE_INTERVAL_SECONDS"] = "-1"
        get_settings.cache_clear()

        with self.assertRaises(ValidationError):
            get_settings()

    def test_embed_reconcile_interval_must_not_be_negative(self) -> None:
        os.environ["EMBED_RECONCILE_INTERVAL_SECONDS"] = "-1"
        get_settings.cache_clear()

        with self.assertRaises(ValidationError):
            get_settings()

    def test_agent_max_steps_is_bounded(self) -> None:
        os.environ["RAG_AGENT_MAX_STEPS"] = "13"
        get_settings.cache_clear()

        with self.assertRaises(ValidationError):
            get_settings()


if __name__ == "__main__":
    unittest.main()
