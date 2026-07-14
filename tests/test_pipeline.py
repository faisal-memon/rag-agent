import unittest
from datetime import datetime, timezone

from app.api.pipeline import _document_row


class PipelineStatusTest(unittest.TestCase):
    def test_document_row_maps_pipeline_fields(self) -> None:
        indexed_at = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        row = (
            7,
            "/documents/example.pdf",
            "example.pdf",
            "application/pdf",
            1234,
            indexed_at,
            indexed_at,
            None,
            "tokenizer-aligned-v1:normalized",
            "mxbai-embed-large-v1",
            4,
        )

        self.assertEqual(
            {
                "id": 7,
                "path": "/documents/example.pdf",
                "filename": "example.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 1234,
                "modified_time": indexed_at,
                "last_indexed_at": indexed_at,
                "missing_since": None,
                "indexing_version": "tokenizer-aligned-v1:normalized",
                "embedding_model": "mxbai-embed-large-v1",
                "chunk_count": 4,
            },
            _document_row(row),
        )


if __name__ == "__main__":
    unittest.main()
