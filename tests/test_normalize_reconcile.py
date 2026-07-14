import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from app.core.config import get_settings
from app.normalize import reconcile


class NormalizeReconcileTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("NORMALIZED_OUTPUT_DIR", None)
        os.environ.pop("NORMALIZE_WATCH_DEBOUNCE_SECONDS", None)
        os.environ.pop("NORMALIZE_RECONCILE_INTERVAL_SECONDS", None)
        os.environ.pop("NORMALIZE_WATCH_STABILITY_MAX_WAIT_SECONDS", None)
        get_settings.cache_clear()

    def test_reconciler_waits_for_queue_item(self) -> None:
        work_queue: Queue[Path] = Queue()
        source = Path("/documents/receipt.pdf")
        work_queue.put(source)
        reconciler = reconcile.Reconciler(get_settings(), work_queue)

        with patch("app.normalize.reconcile.reconcile_path", return_value=True) as reconcile_path:
            reconciler.process_batch()

        reconcile_path.assert_called_once_with(source, get_settings())
        self.assertEqual(set(), reconciler.deferred)

    def test_reconciler_retries_deferred_on_timeout(self) -> None:
        os.environ["NORMALIZE_RECONCILE_INTERVAL_SECONDS"] = "0.001"
        get_settings.cache_clear()
        source = Path("/documents/receipt.pdf")
        reconciler = reconcile.Reconciler(get_settings(), Queue())
        reconciler.deferred.add(source)

        with patch("app.normalize.reconcile.reconcile_path", return_value=True) as reconcile_path:
            reconciler.process_batch()

        reconcile_path.assert_called_once_with(source, get_settings())
        self.assertEqual(set(), reconciler.deferred)

    def test_reconciler_skips_duplicate_queue_items_per_batch(self) -> None:
        work_queue: Queue[Path] = Queue()
        source = Path("/documents/receipt.pdf")
        work_queue.put(source)
        work_queue.put(source)
        reconciler = reconcile.Reconciler(get_settings(), work_queue)

        with patch("app.normalize.reconcile.reconcile_path", return_value=True) as reconcile_path:
            reconciler.process_batch()

        reconcile_path.assert_called_once_with(source, get_settings())

    def test_reconcile_paths_normalizes_supported_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "hello.txt"
            source.write_text("Hello, watcher", encoding="utf-8")

            @dataclass
            class Artifact:
                markdown_path: str = "/normalized/documents/hello.md"
                extraction_backend: str = "passthrough"

            with (
                patch("app.normalize.reconcile.wait_for_stable_file", return_value=True),
                patch("app.normalize.reconcile.file_checksum", return_value="abc123"),
                patch("app.normalize.reconcile.normalize_file", return_value=Artifact()) as normalize_file,
            ):
                deferred = reconcile.reconcile_paths({source}, get_settings())

        normalize_file.assert_called_once_with(source)
        self.assertEqual(set(), deferred)

    def test_reconcile_paths_defers_unstable_supported_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "hello.txt"
            source.write_text("Hello, watcher", encoding="utf-8")

            with (
                patch("app.normalize.reconcile.wait_for_stable_file", return_value=False),
                patch("app.normalize.reconcile.normalize_file") as normalize_file,
            ):
                deferred = reconcile.reconcile_paths({source}, get_settings())

        normalize_file.assert_not_called()
        self.assertEqual({source}, deferred)

    def test_reconcile_paths_deletes_artifacts_for_removed_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            normalized_dir = Path(temp_dir) / "normalized"
            metadata_dir = normalized_dir / "metadata" / "documents"
            markdown_dir = normalized_dir / "documents"
            metadata_dir.mkdir(parents=True)
            markdown_dir.mkdir(parents=True)

            source = Path("/documents/receipt.pdf")
            markdown_path = markdown_dir / "receipt.md"
            metadata_path = metadata_dir / "receipt.json"
            other_metadata_path = metadata_dir / "other.json"
            markdown_path.write_text("# Receipt", encoding="utf-8")
            metadata_path.write_text(
                json.dumps({"source_path": str(source), "markdown_path": str(markdown_path)}),
                encoding="utf-8",
            )
            other_metadata_path.write_text(
                json.dumps({"source_path": "/documents/other.pdf", "markdown_path": "/normalized/other.md"}),
                encoding="utf-8",
            )

            os.environ["NORMALIZED_OUTPUT_DIR"] = str(normalized_dir)
            get_settings.cache_clear()

            deferred = reconcile.reconcile_paths({source}, get_settings())

            self.assertEqual(set(), deferred)
            self.assertFalse(markdown_path.exists())
            self.assertFalse(metadata_path.exists())
            self.assertTrue(other_metadata_path.exists())

    def test_reconcile_paths_skips_current_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            normalized_dir = Path(temp_dir) / "normalized"
            metadata_dir = normalized_dir / "metadata"
            markdown_dir = normalized_dir / "documents"
            source = Path(temp_dir) / "receipt.txt"
            markdown_path = markdown_dir / "receipt.md"
            metadata_path = metadata_dir / "receipt.json"

            metadata_dir.mkdir(parents=True)
            markdown_dir.mkdir(parents=True)
            source.write_text("Hello", encoding="utf-8")
            markdown_path.write_text("# Receipt", encoding="utf-8")
            metadata_path.write_text(
                json.dumps(
                    {
                        "source_path": str(source),
                        "markdown_path": str(markdown_path),
                        "checksum": "abc123",
                        "normalization_version": reconcile.NORMALIZATION_VERSION,
                    }
                ),
                encoding="utf-8",
            )

            os.environ["NORMALIZED_OUTPUT_DIR"] = str(normalized_dir)
            get_settings.cache_clear()

            with (
                patch("app.normalize.reconcile.wait_for_stable_file", return_value=True),
                patch("app.normalize.reconcile.file_checksum", return_value="abc123"),
                patch("app.normalize.reconcile.normalize_file") as normalize_file,
            ):
                deferred = reconcile.reconcile_paths({source}, get_settings())

            self.assertEqual(set(), deferred)
            normalize_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
