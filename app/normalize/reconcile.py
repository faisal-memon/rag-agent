import json
import logging
import time
from pathlib import Path
from queue import Empty, Queue

from app.core.config import Settings
from app.core.files import SUPPORTED_SUFFIXES, file_checksum
from app.normalize.service import NORMALIZATION_VERSION, normalize_file

logger = logging.getLogger("rag-normalize-reconcile")


class Reconciler:
    def __init__(self, settings: Settings, work_queue: Queue[Path]) -> None:
        self.settings = settings
        self.work_queue = work_queue
        self.deferred: set[Path] = set()

    def run_forever(self) -> None:
        while True:
            self.process_batch()

    def process_batch(self) -> None:
        seen: set[Path] = set()

        try:
            path = self.work_queue.get(timeout=self.settings.normalize.reconcile_interval_seconds)
            self._reconcile_once(path, seen)
            self.work_queue.task_done()
        except Empty:
            pass

        while True:
            try:
                path = self.work_queue.get_nowait()
            except Empty:
                break

            self._reconcile_once(path, seen)
            self.work_queue.task_done()

        for path in sorted(self.deferred):
            self._reconcile_once(path, seen)

    def _reconcile_once(self, path: Path, seen: set[Path]) -> None:
        if path in seen:
            return

        seen.add(path)
        try:
            if reconcile_path(path, self.settings):
                self.deferred.discard(path)
            else:
                self.deferred.add(path)
        except Exception as exc:
            _log("Reconcile error", path=str(path), error=str(exc))


def reconcile_paths(paths: set[Path], settings: Settings) -> set[Path]:
    enabled_suffixes = SUPPORTED_SUFFIXES & settings.normalize.enabled_suffixes
    deferred: set[Path] = set()

    for path in sorted(paths):
        if path.suffix.lower() not in enabled_suffixes:
            continue
        try:
            if not reconcile_path(path, settings):
                deferred.add(path)
        except Exception as exc:
            _log("Reconcile error", path=str(path), error=str(exc))

    return deferred


def reconcile_path(path: Path, settings: Settings) -> bool:
    if not path.exists():
        deleted_artifacts = delete_normalized_artifacts_for_source(path, settings)
        _log("Reconciled deleted source", path=str(path), deleted_artifacts=deleted_artifacts)
        return True

    if not path.is_file():
        return True

    if not wait_for_stable_file(path, settings):
        _log("Deferring unstable source", path=str(path))
        return False

    checksum = file_checksum(path)
    if normalized_artifact_is_current(path, checksum, settings):
        _log("Normalized artifact is current", path=str(path))
        return True

    delete_normalized_artifacts_for_source(path, settings)
    artifact = normalize_file(path)
    _log(
        "Reconciled changed source",
        path=str(path),
        markdown_path=artifact.markdown_path,
        extraction_backend=artifact.extraction_backend,
    )
    return True


def normalized_artifact_is_current(source_path: Path, checksum: str, settings: Settings) -> bool:
    for metadata_path in _metadata_paths(settings):
        metadata = _read_metadata(metadata_path)
        if not metadata:
            continue
        if metadata.get("source_path") != str(source_path):
            continue
        markdown_path_value = metadata.get("markdown_path")
        return (
            metadata.get("checksum") == checksum
            and metadata.get("normalization_version") == NORMALIZATION_VERSION
            and bool(markdown_path_value)
            and Path(markdown_path_value).exists()
        )

    return False


def delete_normalized_artifacts_for_source(source_path: Path, settings: Settings) -> int:
    deleted_count = 0
    for metadata_path in _metadata_paths(settings):
        metadata = _read_metadata(metadata_path)
        if not metadata or metadata.get("source_path") != str(source_path):
            continue

        markdown_path_value = metadata.get("markdown_path")
        if markdown_path_value:
            markdown_path = Path(markdown_path_value)
            try:
                markdown_path.unlink(missing_ok=True)
            except OSError as exc:
                _log("Could not delete markdown artifact", markdown_path=str(markdown_path), error=str(exc))

        try:
            metadata_path.unlink(missing_ok=True)
        except OSError as exc:
            _log("Could not delete metadata artifact", metadata_path=str(metadata_path), error=str(exc))
            continue

        deleted_count += 1

    return deleted_count


def wait_for_stable_file(path: Path, settings: Settings) -> bool:
    previous: tuple[int, int] | None = None
    stable_count = 0
    required_checks = settings.normalize.stability_checks
    interval_seconds = settings.normalize.stability_interval_seconds
    deadline = time.monotonic() + settings.normalize.stability_max_wait_seconds
    remaining_seconds = max(0.0, deadline - time.monotonic())

    while remaining_seconds > 0 or previous is None:
        try:
            stats = path.stat()
        except OSError:
            return False

        current = (stats.st_size, stats.st_mtime_ns)
        if current == previous:
            stable_count += 1
        else:
            stable_count = 0
            previous = current

        if stable_count >= required_checks:
            return True

        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            break

        time.sleep(min(interval_seconds, remaining_seconds))

    return False


def _metadata_paths(settings: Settings):
    metadata_dir = settings.common.normalized_output_dir / "metadata"
    if not metadata_dir.exists():
        return
    yield from metadata_dir.rglob("*.json")


def _read_metadata(metadata_path: Path) -> dict | None:
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log("Skipping unreadable metadata", metadata_path=str(metadata_path), error=str(exc))
        return None


def _log(message: str, **fields) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("%s%s", message, f" {details}" if details else "")
