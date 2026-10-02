import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.api import routes
from app.agent.api.schemas import AgentQueryRequest, AgentRuntimeSettings
from app.agent.config import get_api_settings
from app.core.config import read_runtime_settings, write_runtime_settings


class SettingsApiTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("RAG_SETTINGS_PATH", None)
        get_api_settings.cache_clear()

    def test_update_settings_preserves_other_runtime_sections_and_reloads_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime-settings.json"
            os.environ["RAG_SETTINGS_PATH"] = str(path)
            write_runtime_settings({"normalize": {"backend": "docling"}})
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
            payload = AgentRuntimeSettings(
                llm_provider="llamacpp",
                openai_chat_model="gpt-4.1-mini",
                llamacpp_base_url="http://model.local/v1",
                llamacpp_chat_model="gemma-4",
                query_limit=10,
                agent_max_steps=5,
            )

            with patch("app.agent.api.routes.Agent") as agent_class:
                result = routes.update_settings(request, payload)

            saved_settings = read_runtime_settings()

        self.assertEqual("gemma-4", result.llamacpp_chat_model)
        self.assertEqual(10, result.query_limit)
        self.assertEqual({"backend": "docling"}, saved_settings["normalize"])
        self.assertEqual("gemma-4", saved_settings["api"]["llamacpp_chat_model"])
        agent_class.return_value.startup.assert_called_once()

    def test_agent_stream_emits_progress_before_the_final_response(self) -> None:
        class StreamingAgent:
            def answer(self, _question, history=None, on_progress=None):
                self.history = history
                on_progress({"type": "thinking", "message": "Thinking"})
                on_progress({"type": "tool_call", "tool": "get_school_lunch", "arguments": {}})
                return {
                    "answer": "Lunch is pasta.",
                    "plan": [],
                    "tool_results": [],
                    "reasoning": [],
                    "debug": [],
                    "citations": [],
                }

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(agent=StreamingAgent())),
            headers={"Remote-User": "test-user"},
        )
        with patch("app.agent.api.routes.load_or_create", return_value=("00000000-0000-0000-0000-000000000001", [])), patch("app.agent.api.routes.append_messages"):
            response = routes.stream_agent_query(request, AgentQueryRequest(question="What is lunch?"))

            async def read_stream():
                return [chunk async for chunk in response.body_iterator]

            chunks = asyncio.run(read_stream())
        body = b"".join(chunk if isinstance(chunk, bytes) else chunk.encode() for chunk in chunks).decode()

        self.assertIn('"type":"thinking"', body)
        self.assertIn('"type":"tool_call"', body)
        self.assertIn('"type":"complete"', body)
