import unittest

from app.agent.api.schemas import AgentQueryRequest


class AgentQueryRequestTest(unittest.TestCase):
    def test_request_contains_only_conversation_and_question(self) -> None:
        request = AgentQueryRequest(question="hi", conversation_id="abc")

        self.assertEqual("abc", request.conversation_id)
        self.assertEqual("hi", request.question)
