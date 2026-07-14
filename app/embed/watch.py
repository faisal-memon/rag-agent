import logging
from pathlib import Path
from queue import Queue

from watchfiles import Change, watch

from app.core.config import Settings

NORMALIZED_SUFFIXES = {".json", ".md"}

LOGGER_NAME = "rag-embed-watch"
logger = logging.getLogger(LOGGER_NAME)


def watch_normalized(settings: Settings, work_queue: Queue[Path]) -> None:
    normalized_dir = settings.common.normalized_output_dir
    normalized_dir.mkdir(parents=True, exist_ok=True)

    _log(
        "Starting embed watcher",
        normalized_dir=str(normalized_dir),
        enabled_suffixes=",".join(sorted(NORMALIZED_SUFFIXES)),
        debounce_seconds=settings.embed.watch_debounce_seconds,
    )

    for changes in watch(
        str(normalized_dir),
        recursive=True,
        debounce=int(settings.embed.watch_debounce_seconds * 1000),
    ):
        for change, raw_path in changes:
            path = Path(raw_path)
            if _should_queue(change, path):
                work_queue.put(path)
                _log("Queued normalized artifact event", path=str(path), change=change.name)


def _should_queue(change: Change, path: Path) -> bool:
    return change in {Change.added, Change.modified, Change.deleted} and path.suffix.lower() in NORMALIZED_SUFFIXES


def _log(message: str, **fields) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("%s%s", message, f" {details}" if details else "")
