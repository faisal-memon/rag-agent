import logging
from pathlib import Path
from queue import Empty, Queue

from app.config import Settings
from app.embed.service import reindex_artifact

LOGGER_NAME = "rag-embed-reconcile"
logger = logging.getLogger(LOGGER_NAME)


class Reconciler:
    def __init__(self, settings: Settings, work_queue: Queue[Path]) -> None:
        self.settings = settings
        self.work_queue = work_queue
        self.pending_paths: set[Path] = set()

    def run_forever(self) -> None:
        while True:
            self.process_cycle()

    def process_cycle(self) -> None:
        event_count = 0

        try:
            path = self.work_queue.get(timeout=self.settings.embed.reconcile_interval_seconds)
            event_count += 1
            self.pending_paths.add(path)
            self._mark_event_done(path)
        except Empty:
            pass

        while True:
            try:
                path = self.work_queue.get_nowait()
            except Empty:
                break

            event_count += 1
            self.pending_paths.add(path)
            self._mark_event_done(path)

        if event_count:
            _log("Queued normalized artifact changes", event_count=event_count)

        if not self.pending_paths:
            return

        for path in sorted(self.pending_paths):
            try:
                result = reindex_artifact(path)
            except Exception as exc:
                _log("Embed reconcile failed", path=str(path), error=str(exc))
                continue

            if not result.get("errors"):
                self.pending_paths.remove(path)
            _log(
                "Embed reconcile completed",
                path=str(path),
                indexed_documents=result.get("indexed_documents"),
                indexed_chunks=result.get("indexed_chunks"),
                skipped_documents=result.get("skipped_documents"),
                metadata_updated_documents=result.get("metadata_updated_documents"),
                missing_marked_documents=result.get("missing_marked_documents"),
                deleted_documents=result.get("deleted_documents"),
                errors=len(result.get("errors", [])),
            )

    def _mark_event_done(self, path: Path) -> None:
        _log("Processing normalized artifact event", path=str(path))
        self.work_queue.task_done()


def _log(message: str, **fields) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("%s%s", message, f" {details}" if details else "")
