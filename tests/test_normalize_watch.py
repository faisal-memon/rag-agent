import tempfile
import unittest
from pathlib import Path

from watchfiles import Change

from app.normalize import watch


class NormalizeWatchTest(unittest.TestCase):
    def test_should_queue_supported_created_modified_or_deleted_files(self) -> None:
        path = Path("/documents/receipt.pdf")

        self.assertTrue(watch._should_queue(Change.added, path, {".pdf"}))
        self.assertTrue(watch._should_queue(Change.modified, path, {".pdf"}))
        self.assertTrue(watch._should_queue(Change.deleted, path, {".pdf"}))

    def test_should_not_queue_unsupported_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            unsupported = Path(temp_dir) / "photo.gif"
            unsupported.write_bytes(b"gif")

            self.assertFalse(watch._should_queue(Change.added, unsupported, {".pdf"}))
            self.assertFalse(watch._should_queue(Change.deleted, unsupported, {".pdf"}))


if __name__ == "__main__":
    unittest.main()
