import logging
from pathlib import Path
from queue import Queue
from threading import Thread

from app.core.config import get_settings
from app.embed.reconcile import Reconciler
from app.embed.watch import watch_normalized


def main() -> None:
    _setup_logging()
    settings = get_settings()
    work_queue: Queue[Path] = Queue()
    watcher = Thread(target=watch_normalized, args=(settings, work_queue), daemon=True)
    watcher.start()

    Reconciler(settings, work_queue).run_forever()


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


if __name__ == "__main__":
    main()
