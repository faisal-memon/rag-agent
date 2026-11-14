import unittest
from unittest.mock import AsyncMock, patch

from app.agent.config import ApiSettings
from app.agent.piper_service import PiperService


class PiperServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_synthesis_uses_the_configured_voice(self) -> None:
        settings = ApiSettings(piper_host="piper-one", piper_port=10200, piper_voice="en_GB-alba-medium")
        with patch("app.agent.piper_service.PiperClient") as client_class:
            client_class.return_value.synthesize_flac = AsyncMock(return_value=b"audio")
            service = PiperService(settings)

            audio = await service.synthesize_flac("Hello")

        self.assertEqual(b"audio", audio)
        client_class.assert_called_once_with("piper-one", 10200, "en_GB-alba-medium")
        client_class.return_value.synthesize_flac.assert_awaited_once_with("Hello")

    async def test_settings_update_replaces_the_configured_client(self) -> None:
        initial = ApiSettings(piper_host="piper-one", piper_port=10200, piper_voice="en_GB-alba-medium")
        updated = ApiSettings(piper_host="piper-two", piper_port=10300, piper_voice="en_US-lessac-medium")
        with patch("app.agent.piper_service.PiperClient") as client_class:
            service = PiperService(initial)
            service.update_settings(updated)

        self.assertEqual(
            [
                (("piper-one", 10200, "en_GB-alba-medium"), {}),
                (("piper-two", 10300, "en_US-lessac-medium"), {}),
            ],
            client_class.call_args_list,
        )
