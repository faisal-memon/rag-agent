"""Create and manage the configured ESPHome device and its voice client."""

from __future__ import annotations

from app.agent.agent import Agent
from app.agent.config import ApiSettings
from app.agent.esphome_devices import ESPHomeDevice
from app.agent.esphome_voice import ESPHomeVoiceClient
from app.agent.piper_service import PiperService


class ESPHomeDeviceManager:
    """Own configured device instances; each device creates its voice client."""

    def __init__(
        self,
        settings: ApiSettings,
        agent: Agent | None = None,
        piper_service: PiperService | None = None,
    ) -> None:
        self._settings = settings
        self._agent = agent
        self._piper_service = piper_service
        self._devices: dict[str, ESPHomeDevice] = {}

    async def start(self) -> None:
        """Create the configured device and start its reconnect lifecycle."""
        host = self._settings.esphome_device_host or "default"
        device = ESPHomeDevice(
            self._settings,
            voice_client_factory=self._create_voice_client,
        )
        self._devices[host] = device
        await device.start()

    async def stop(self) -> None:
        """Stop all managed device connections."""
        for device in self._devices.values():
            await device.stop()

    def update_settings(self, settings: ApiSettings) -> None:
        """Apply settings to managed devices and their voice clients."""
        self._settings = settings
        for device in self._devices.values():
            device.update_settings(settings)

    def status(self) -> dict:
        """Return status for the configured speaker, if one is managed."""
        if self._devices:
            return next(iter(self._devices.values())).status()
        return {
            "device_id": None,
            "display_name": "ESPHome speaker",
            "host": self._settings.esphome_device_host,
            "port": self._settings.esphome_device_port,
            "connected": False,
        }

    def _create_voice_client(self, device: ESPHomeDevice) -> ESPHomeVoiceClient:
        """Build the voice behavior after the physical device is registered."""
        return ESPHomeVoiceClient(
            self._settings,
            self._agent,
            self._piper_service,
            device=device,
        )
