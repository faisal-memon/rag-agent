import tempfile
import unittest
from pathlib import Path

from watchfiles import Change

from app.embed import watch


class EmbedWatchTest(unittest.TestCase):
    def test_should_queue_normalized_markdown_or_metadata_events(self) -> None:
        self.assertTrue(watch._should_queue(Change.added, Path("/normalized/documents/receipt.md")))
        self.assertTrue(watch._should_queue(Change.modified, Path("/normalized/metadata/receipt.json")))
        self.assertTrue(watch._should_queue(Change.deleted, Path("/normalized/documents/receipt.md")))

    def test_should_not_queue_unrelated_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            unsupported = Path(temp_dir) / "receipt.tmp"
            unsupported.write_text("temporary", encoding="utf-8")

            self.assertFalse(watch._should_queue(Change.added, unsupported))
            self.assertFalse(watch._should_queue(Change.deleted, unsupported))


if __name__ == "__main__":
    unittest.main()
