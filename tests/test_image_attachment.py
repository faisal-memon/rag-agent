"""Checks for bounded image attachments in web agent requests."""

import base64
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from app.agent.api.schemas import AgentQueryRequest
from app.agent.agent import _complete_text


PNG = b"\x89PNG\r\n\x1a\n" + b"test image bytes"


class ImageAttachmentTest(unittest.TestCase):
    def test_valid_png_is_accepted(self) -> None:
        data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
        request = AgentQueryRequest(question="What is in this picture?", image_data_url=data_url)
        self.assertEqual(data_url, request.image_data_url)

    def test_mislabeled_and_oversized_files_are_rejected(self) -> None:
        mislabeled = "data:image/jpeg;base64," + base64.b64encode(PNG).decode()
        with self.assertRaises(ValidationError):
            AgentQueryRequest(question="Look", image_data_url=mislabeled)
        oversized = "data:image/png;base64," + base64.b64encode(PNG + b"0" * (5 * 1024 * 1024)).decode()
        with self.assertRaises(ValidationError):
            AgentQueryRequest(question="Look", image_data_url=oversized)

    def test_llamacpp_receives_text_and_image_together(self) -> None:
        sent = {}

        def create(**kwargs):
            sent.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Seen"))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
        answer = _complete_text(
            client, "gemma", "system", "What is in this picture?",
            SimpleNamespace(llm_provider="llamacpp"), image_data_url=data_url,
        )
        self.assertEqual("Seen", answer)
        self.assertEqual(data_url, sent["messages"][1]["content"][1]["image_url"]["url"])


if __name__ == "__main__":
    unittest.main()
