import os
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import call, patch

from app.config import get_settings
from app.embed import reconcile


class EmbedReconcileTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("EMBED_RECONCILE_INTERVAL_SECONDS", None)
        get_settings.cache_clear()

    def test_reconciler_reindexes_queued_artifacts(self) -> None:
        work_queue: Queue[Path] = Queue()
        work_queue.put(Path("/normalized/documents/receipt.md"))
        reconciler = reconcile.Reconciler(get_settings(), work_queue)

        with patch("app.embed.reconcile.reindex_artifact", return_value={"errors": []}) as reindex_artifact:
            reconciler.process_cycle()

        reindex_artifact.assert_called_once_with(Path("/normalized/documents/receipt.md"))
        self.assertEqual(set(), reconciler.pending_paths)

    def test_reconciler_retries_failed_artifacts(self) -> None:
        os.environ["EMBED_RECONCILE_INTERVAL_SECONDS"] = "0.001"
        get_settings.cache_clear()
        reconciler = reconcile.Reconciler(get_settings(), Queue())
        reconciler.pending_paths.add(Path("/normalized/documents/receipt.md"))

        with patch("app.embed.reconcile.reindex_artifact", return_value={"errors": [{"error": "boom"}]}) as reindex_artifact:
            reconciler.process_cycle()

        reindex_artifact.assert_called_once_with(Path("/normalized/documents/receipt.md"))
        self.assertEqual({Path("/normalized/documents/receipt.md")}, reconciler.pending_paths)

    def test_reconciler_does_nothing_when_idle(self) -> None:
        os.environ["EMBED_RECONCILE_INTERVAL_SECONDS"] = "0.001"
        get_settings.cache_clear()
        reconciler = reconcile.Reconciler(get_settings(), Queue())

        with patch("app.embed.reconcile.reindex_artifact") as reindex_artifact:
            reconciler.process_cycle()

        reindex_artifact.assert_not_called()

    def test_reconciler_processes_pending_artifacts_one_call_at_a_time(self) -> None:
        os.environ["EMBED_RECONCILE_INTERVAL_SECONDS"] = "0.001"
        get_settings.cache_clear()
        reconciler = reconcile.Reconciler(get_settings(), Queue())
        first_path = Path("/normalized/documents/a.md")
        second_path = Path("/normalized/documents/b.md")
        reconciler.pending_paths.update({second_path, first_path})

        with patch("app.embed.reconcile.reindex_artifact", return_value={"errors": []}) as reindex_artifact:
            reconciler.process_cycle()

        self.assertEqual([call(first_path), call(second_path)], reindex_artifact.call_args_list)
        self.assertEqual(set(), reconciler.pending_paths)


if __name__ == "__main__":
    unittest.main()
