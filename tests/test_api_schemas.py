import unittest

from app.agent.api.schemas import AgentQueryRequest


class AgentQueryRequestTest(unittest.TestCase):
    def test_ignores_empty_in_progress_history_messages(self) -> None:
        request = AgentQueryRequest(
            question="hi",
            history=[
                {"role": "user", "content": "previous"},
                {"role": "assistant", "content": ""},
                {"role": "assistant", "content": "  "},
            ],
        )

        self.assertEqual([{"role": "user", "content": "previous"}], [message.model_dump() for message in request.history])

