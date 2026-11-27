import os
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import call, patch

from app.embed.config import get_embed_settings
from app.embed import reconcile


class EmbedReconcileTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("EMBED_RECONCILE_INTERVAL_SECONDS", None)
        get_embed_settings.cache_clear()

    def test_full_scan_repeats_after_startup_to_close_watcher_race(self) -> None:
        reconciler = reconcile.Reconciler(get_embed_settings(), Queue())
        with (
            patch("app.embed.reconcile.monotonic", side_effect=[100.0, 200.0]),
            patch("app.embed.reconcile.reindex_source", return_value={"indexed_documents": 1, "skipped_documents": 0, "missing_marked_documents": 0, "errors": []}) as scan,
        ):
            reconciler.run_full_scan()
            self.assertEqual(160.0, reconciler.next_full_scan)
            reconciler.run_full_scan()

        self.assertEqual(3800.0, reconciler.next_full_scan)
        self.assertEqual(2, scan.call_count)

    def test_full_scan_retries_database_unavailability_soon(self) -> None:
        reconciler = reconcile.Reconciler(get_embed_settings(), Queue())
        with (
            patch("app.embed.reconcile.monotonic", return_value=100.0),
            patch("app.embed.reconcile.reindex_source", side_effect=OSError("database unavailable")),
            self.assertLogs("rag-embed-reconcile", level="ERROR"),
        ):
            reconciler.run_full_scan()

        self.assertEqual(130.0, reconciler.next_full_scan)
        self.assertEqual(0, reconciler.completed_full_scans)

    def test_reconciler_reindexes_queued_artifacts(self) -> None:
        work_queue: Queue[Path] = Queue()
        work_queue.put(Path("/normalized/documents/receipt.md"))
        reconciler = reconcile.Reconciler(get_embed_settings(), work_queue)

        with patch("app.embed.reconcile.reindex_artifact", return_value={"errors": []}) as reindex_artifact:
            reconciler.process_cycle()

        reindex_artifact.assert_called_once_with(Path("/normalized/documents/receipt.md"))
        self.assertEqual(set(), reconciler.pending_paths)

    def test_reconciler_retries_failed_artifacts(self) -> None:
        os.environ["EMBED_RECONCILE_INTERVAL_SECONDS"] = "0.001"
        get_embed_settings.cache_clear()
        reconciler = reconcile.Reconciler(get_embed_settings(), Queue())
        reconciler.pending_paths.add(Path("/normalized/documents/receipt.md"))

        with patch("app.embed.reconcile.reindex_artifact", return_value={"errors": [{"error": "boom"}]}) as reindex_artifact:
            reconciler.process_cycle()

        reindex_artifact.assert_called_once_with(Path("/normalized/documents/receipt.md"))
        self.assertEqual({Path("/normalized/documents/receipt.md")}, reconciler.pending_paths)

    def test_reconciler_does_nothing_when_idle(self) -> None:
        os.environ["EMBED_RECONCILE_INTERVAL_SECONDS"] = "0.001"
        get_embed_settings.cache_clear()
        reconciler = reconcile.Reconciler(get_embed_settings(), Queue())

        with patch("app.embed.reconcile.reindex_artifact") as reindex_artifact:
            reconciler.process_cycle()

        reindex_artifact.assert_not_called()

    def test_reconciler_processes_pending_artifacts_one_call_at_a_time(self) -> None:
        os.environ["EMBED_RECONCILE_INTERVAL_SECONDS"] = "0.001"
        get_embed_settings.cache_clear()
        reconciler = reconcile.Reconciler(get_embed_settings(), Queue())
        first_path = Path("/normalized/documents/a.md")
        second_path = Path("/normalized/documents/b.md")
        reconciler.pending_paths.update({second_path, first_path})

        with patch("app.embed.reconcile.reindex_artifact", return_value={"errors": []}) as reindex_artifact:
            reconciler.process_cycle()

        self.assertEqual([call(first_path), call(second_path)], reindex_artifact.call_args_list)
        self.assertEqual(set(), reconciler.pending_paths)


if __name__ == "__main__":
    unittest.main()
