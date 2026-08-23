import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.api import routes
from app.agent.api.schemas import AgentRuntimeSettings
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
