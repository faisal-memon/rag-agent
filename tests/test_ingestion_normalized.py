import json
import os
import tempfile
import unittest
from pathlib import Path

from app.config import get_settings
from app.embed.service import _current_indexing_strategy, _normalized_candidate_from_path, _normalized_metadata_path


class NormalizedIngestionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original_env = {
            "NEXTCLOUD_SOURCE_DIR": os.environ.get("NEXTCLOUD_SOURCE_DIR"),
            "NORMALIZED_OUTPUT_DIR": os.environ.get("NORMALIZED_OUTPUT_DIR"),
        }
        get_settings.cache_clear()

    def tearDown(self) -> None:
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    def test_normalized_metadata_path_mirrors_markdown_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            normalized_dir = Path(temp_dir) / "normalized"
            os.environ["NORMALIZED_OUTPUT_DIR"] = str(normalized_dir)
            get_settings.cache_clear()

            markdown_path = normalized_dir / "documents" / "Taxes" / "2017 Taxes" / "receipt-abc.md"

            metadata_path = _normalized_metadata_path(markdown_path)

        self.assertEqual(metadata_path, normalized_dir / "metadata" / "Taxes" / "2017 Taxes" / "receipt-abc.json")

    def test_normalized_candidate_uses_source_metadata_for_document_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "source"
            normalized_dir = Path(temp_dir) / "normalized"
            source_path = source_dir / "Taxes" / "receipt.pdf"
            markdown_path = normalized_dir / "documents" / "Taxes" / "receipt-abc.md"
            metadata_path = normalized_dir / "metadata" / "Taxes" / "receipt-abc.json"
            source_path.parent.mkdir(parents=True)
            markdown_path.parent.mkdir(parents=True)
            metadata_path.parent.mkdir(parents=True)
            source_path.write_bytes(b"original pdf")
            markdown_path.write_text("# Receipt\n\nTotal: $12.34\n", encoding="utf-8")
            metadata_path.write_text(
                json.dumps(
                    {
                        "source_path": str(source_path),
                        "source_name": "receipt.pdf",
                        "checksum": "abc123",
                        "mime_type": "application/pdf",
                        "normalization_version": "normalized-artifacts-v2",
                        "extraction_backend": "docling",
                        "normalized_at": "2026-05-04T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            os.environ["NEXTCLOUD_SOURCE_DIR"] = str(source_dir)
            os.environ["NORMALIZED_OUTPUT_DIR"] = str(normalized_dir)
            get_settings.cache_clear()

            candidate = _normalized_candidate_from_path(markdown_path)

        self.assertEqual(candidate["path"], str(source_path))
        self.assertEqual(candidate["filename"], "receipt.pdf")
        self.assertEqual(candidate["mime_type"], "application/pdf")
        self.assertEqual(candidate["checksum"], "abc123")
        self.assertEqual(candidate["content"], "# Receipt\n\nTotal: $12.34\n")
        self.assertEqual(candidate["chunk_metadata"]["index_source"], "normalized")
        self.assertEqual(candidate["chunk_metadata"]["extraction_backend"], "docling")

    def test_indexing_strategy_is_normalized(self) -> None:
        get_settings.cache_clear()

        strategy = _current_indexing_strategy()

        self.assertTrue(strategy["indexing_version"].endswith(":normalized"))


if __name__ == "__main__":
    unittest.main()
