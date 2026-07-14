import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import get_settings
from app.normalize.service import normalize_file


class FakeDoclingDocument:
    def export_to_markdown(self) -> str:
        return "## Parsed by Docling\n\n| item | total |\n| --- | --- |\n| paint | $12.34 |"


class FakeDoclingResult:
    status = "success"
    document = FakeDoclingDocument()


class FakeDoclingConverter:
    def convert(self, source: str) -> FakeDoclingResult:
        self.source = source
        return FakeDoclingResult()


class NormalizeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original_env = {
            "NORMALIZED_OUTPUT_DIR": os.environ.get("NORMALIZED_OUTPUT_DIR"),
            "NEXTCLOUD_SOURCE_DIR": os.environ.get("NEXTCLOUD_SOURCE_DIR"),
            "NORMALIZATION_BACKEND": os.environ.get("NORMALIZATION_BACKEND"),
        }
        get_settings.cache_clear()

    def tearDown(self) -> None:
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    def test_text_file_normalizes_to_markdown_with_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self._set_normalized_output_dir(temp_dir)
            source = Path(temp_dir) / "hello.txt"
            source.write_text("Hello, world", encoding="utf-8")

            artifact = normalize_file(source)

            markdown = Path(artifact.markdown_path).read_text(encoding="utf-8")
            metadata = json.loads(Path(artifact.metadata_path).read_text(encoding="utf-8"))

        self.assertEqual(markdown, "# hello.txt\n\nHello, world\n")
        self.assertEqual(metadata["extraction_backend"], "passthrough")
        self.assertEqual(metadata["source_name"], "hello.txt")

    def test_normalized_artifacts_preserve_source_directory_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "source"
            output_dir = Path(temp_dir) / "normalized"
            nested_dir = source_dir / "Taxes" / "2017 Taxes"
            nested_dir.mkdir(parents=True)
            self._set_normalized_output_dir(str(output_dir), source_dir=str(source_dir))
            source = nested_dir / "receipt.txt"
            source.write_text("Paint and supplies", encoding="utf-8")

            artifact = normalize_file(source)

            markdown_path = Path(artifact.markdown_path)
            metadata_path = Path(artifact.metadata_path)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(markdown_path.parent, output_dir / "documents" / "Taxes" / "2017 Taxes")
        self.assertEqual(metadata_path.parent, output_dir / "metadata" / "Taxes" / "2017 Taxes")
        self.assertEqual(metadata["markdown_path"], str(markdown_path))

    def test_docling_backend_exports_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self._set_normalized_output_dir(temp_dir)
            source = Path(temp_dir) / "receipt.pdf"
            source.write_bytes(b"fake pdf bytes")

            with patch("app.normalize.service._get_docling_converter", return_value=FakeDoclingConverter()):
                artifact = normalize_file(source)

            markdown = Path(artifact.markdown_path).read_text(encoding="utf-8")
            metadata = json.loads(Path(artifact.metadata_path).read_text(encoding="utf-8"))

        self.assertIn("## Parsed by Docling", markdown)
        self.assertIn("| paint | $12.34 |", markdown)
        self.assertEqual(metadata["extraction_backend"], "docling")

    def _set_normalized_output_dir(self, output_dir: str, source_dir: str | None = None) -> None:
        os.environ["NORMALIZED_OUTPUT_DIR"] = output_dir
        if source_dir is not None:
            os.environ["NEXTCLOUD_SOURCE_DIR"] = source_dir
        os.environ["NORMALIZATION_BACKEND"] = "docling"
        get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
