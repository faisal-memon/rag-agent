import logging
from pathlib import Path
from queue import Queue

from watchfiles import Change, watch

from app.core.config import Settings
from app.core.files import SUPPORTED_SUFFIXES


LOGGER_NAME = "rag-normalize-watch"
logger = logging.getLogger(LOGGER_NAME)


def watch_source(settings: Settings, work_queue: Queue[Path]) -> None:
    source_dir = settings.common.nextcloud_source_dir
    source_dir.mkdir(parents=True, exist_ok=True)
    enabled_suffixes = SUPPORTED_SUFFIXES & settings.normalize.enabled_suffixes

    _log(
        "Starting normalization watcher",
        source_dir=str(source_dir),
        enabled_suffixes=",".join(sorted(enabled_suffixes)),
        debounce_seconds=settings.normalize.watch_debounce_seconds,
        stability_max_wait_seconds=settings.normalize.stability_max_wait_seconds,
    )

    for changes in watch(
        str(source_dir),
        recursive=True,
        debounce=int(settings.normalize.watch_debounce_seconds * 1000),
    ):
        for change, raw_path in changes:
            path = Path(raw_path)
            if _should_queue(change, path, enabled_suffixes):
                work_queue.put(path)
                _log("Queued filesystem event", path=str(path), change=change.name)


def _should_queue(change: Change, path: Path, enabled_suffixes: set[str]) -> bool:
    return change in {Change.added, Change.modified, Change.deleted} and path.suffix.lower() in enabled_suffixes


def _log(message: str, **fields) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("%s%s", message, f" {details}" if details else "")
