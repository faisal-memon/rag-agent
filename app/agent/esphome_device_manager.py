"""Create and manage the configured ESPHome device and its voice client."""

from __future__ import annotations

from app.agent.agent import Agent
from app.agent.config import ApiSettings
from app.agent.esphome_devices import ESPHomeDevice
from app.agent.esphome_voice import ESPHomeVoiceClient
from app.agent.piper_service import PiperService


class ESPHomeDeviceManager:
    """Track configured connections and register devices only while they are ready."""

    def __init__(
        self,
        settings: ApiSettings,
        agent: Agent | None = None,
        piper_service: PiperService | None = None,
    ) -> None:
        self._settings = settings
        self._agent = agent
        self._piper_service = piper_service
        # Connections exist so devices can reconnect; _devices contains only ready ones.
        self._connections: dict[str, ESPHomeDevice] = {}
        self._devices: dict[str, ESPHomeDevice] = {}

    async def start(self) -> None:
        """Create the configured device and start its reconnect lifecycle."""
        host = self._settings.esphome_device_host.strip()
        if not host:
            return
        device = ESPHomeDevice(
            self._settings,
            voice_client_factory=self._create_voice_client,
            on_registered=self._register_device,
            on_unregistered=self._unregister_device,
        )
        self._connections[host] = device
        await device.start()

    async def stop(self) -> None:
        """Stop all managed device connections."""
        for device in self._connections.values():
            await device.stop()
        self._devices.clear()

    def update_settings(self, settings: ApiSettings) -> None:
        """Apply settings to managed devices and their voice clients."""
        self._settings = settings
        for device in self._connections.values():
            device.update_settings(settings)

    def status(self) -> dict:
        """Return one device status for compatibility with existing callers."""
        statuses = self.statuses()
        return statuses[0] if statuses else self._pending_status()

    def statuses(self) -> list[dict]:
        """Return connection status for each configured device."""
        return [device.status() for device in self._connections.values()]

    def _pending_status(self) -> dict:
        return {
            "device_id": None,
            "display_name": "ESPHome speaker",
            "host": self._settings.esphome_device_host,
            "port": self._settings.esphome_device_port,
            "connected": False,
        }

    def _register_device(self, device: ESPHomeDevice) -> None:
        """Add a connected device after its voice client is ready."""
        key = device.device_id or device.settings.esphome_device_host
        self._devices[key] = device

    def _unregister_device(self, device: ESPHomeDevice) -> None:
        """Remove a disconnected device while retaining its reconnect connection."""
        key = device.device_id or device.settings.esphome_device_host
        if self._devices.get(key) is device:
            del self._devices[key]

    def _create_voice_client(self, device: ESPHomeDevice) -> ESPHomeVoiceClient:
        """Build the voice behavior after the physical device is registered."""
        return ESPHomeVoiceClient(
            self._settings,
            self._agent,
            self._piper_service,
            device=device,
        )
