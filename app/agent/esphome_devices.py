"""Manage an ESPHome device's Native API connection and voice subscription."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

import aioesphomeapi

from app.agent import voice_devices
from app.agent.config import ApiSettings

log = logging.getLogger(__name__)

VoiceStartHandler = Callable[..., Awaitable[int]]
VoiceStopHandler = Callable[..., Awaitable[None]]
VoiceAudioHandler = Callable[..., Awaitable[None]]
DeviceLifecycleHandler = Callable[["ESPHomeDevice"], None]


class DeviceVoiceClient(Protocol):
    """Voice behavior owned by a connected ESPHome device."""

    async def on_device_ready(self) -> None:
        """Initialize voice handling and subscribe to this device's callbacks."""
        ...

    async def on_disconnect(self, expected: bool) -> None:
        """Handle loss of the device connection and reset voice state."""
        ...

    async def stop(self) -> None:
        """Release voice resources without stopping the device connection."""
        ...

    def update_settings(self, settings: ApiSettings) -> None:
        """Apply updated runtime settings to voice handling."""
        ...


VoiceClientFactory = Callable[["ESPHomeDevice"], DeviceVoiceClient]


class ESPHomeDevice:
    """Own one device connection and create its voice client when it is registered."""

    def __init__(
        self,
        settings: ApiSettings,
        *,
        voice_client_factory: VoiceClientFactory,
        on_registered: DeviceLifecycleHandler,
        on_unregistered: DeviceLifecycleHandler,
    ) -> None:
        self.settings = settings
        self._voice_client_factory = voice_client_factory
        self._on_registered = on_registered
        self._on_unregistered = on_unregistered
        self.voice_client: DeviceVoiceClient | None = None
        self.client: aioesphomeapi.APIClient | None = None
        self._reconnect: aioesphomeapi.ReconnectLogic | None = None
        self._unsubscribe_voice: Callable[[], None] | None = None
        self.device_id: str | None = None
        self.display_name = settings.esphome_device_name or "ESPHome speaker"
        self.media_player_key: int | None = None
        self.connected = False

    async def start(self) -> None:
        """Start reconnecting to this device if it is configured."""
        if not self.settings.esphome_device_host:
            log.info("ESPHome voice client disabled; ESPHOME_DEVICE_HOST is unset")
            return

        self.client = aioesphomeapi.APIClient(
            self.settings.esphome_device_host,
            self.settings.esphome_device_port,
            client_info="RAG Agent",
            noise_psk=self.settings.esphome_api_key or None,
            expected_name=self.settings.esphome_device_name or None,
        )
        self._reconnect = aioesphomeapi.ReconnectLogic(
            client=self.client,
            name=self.settings.esphome_device_host,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            on_connect_error=self._on_connect_error,
            zeroconf_wake=False,
        )
        await self._reconnect.start()
        log.info(
            "ESPHome device connection started for %s:%s",
            self.settings.esphome_device_host,
            self.settings.esphome_device_port,
        )

    async def stop(self) -> None:
        """Stop reconnecting, remove callbacks, and close the API connection."""
        if self.voice_client is not None:
            await self.voice_client.stop()
        if self._reconnect is not None:
            await self._reconnect.stop()
        self._unsubscribe()
        if self.client is not None:
            await self.client.disconnect()

    def status(self) -> dict:
        """Return this device's configured and live connection details."""
        return {
            "device_id": self.device_id,
            "display_name": self.display_name,
            "host": self.settings.esphome_device_host,
            "port": self.settings.esphome_device_port,
            "connected": self.connected,
        }

    async def _on_connect(self) -> None:
        assert self.client is not None

        # Check that the connected device supports voice and Native API audio.
        info = await self.client.device_info()
        features = info.voice_assistant_feature_flags_compat(self.client.api_version)
        if not features & aioesphomeapi.VoiceAssistantFeature.VOICE_ASSISTANT:
            log.error("ESPHome device %s has no Voice Assistant capability", info.name)
            return
        if not features & aioesphomeapi.VoiceAssistantFeature.API_AUDIO:
            log.error("ESPHome device %s does not support Native API audio", info.name)
            return

        # Find the device-assigned media-player entity identifier.
        entities, _ = await self.client.list_entities_services()
        media_player = next(
            (
                entity
                for entity in entities
                if isinstance(entity, aioesphomeapi.MediaPlayerInfo)
                and entity.object_id == "media_player"
            ),
            None,
        )
        self.media_player_key = media_player.key if media_player is not None else None
        if self.settings.esphome_media_url_base and self.media_player_key is None:
            log.error("ESPHome device %s has no Media Player entity", info.name)

        self.device_id = _stable_device_id(
            info.mac_address, self.settings.esphome_device_host
        )
        self.display_name = info.name or self.display_name
        self.connected = True

        # Register this speaker for history and activity records.
        try:
            await asyncio.to_thread(
                voice_devices.register_device,
                self.device_id,
                self.display_name,
                self.settings.esphome_device_host,
                self.settings.esphome_device_port,
            )
        except Exception:
            log.exception("Could not persist ESPHome device registration")
            self.device_id = None

        # The device creates its voice client only after connection and registration.
        if self.voice_client is None:
            self.voice_client = self._voice_client_factory(self)
        await self.voice_client.on_device_ready()
        self._on_registered(self)

        log.info(
            "Connected to ESPHome device %s (%s); voice client ready",
            info.name,
            info.mac_address,
        )

    async def _on_disconnect(self, expected: bool) -> None:
        self.connected = False
        self._unsubscribe()
        if self.voice_client is not None:
            await self.voice_client.on_disconnect(expected)
            self._on_unregistered(self)

    async def _on_connect_error(self, error: Exception) -> None:
        log.warning("Could not connect to ESPHome device: %s", error)

    def _unsubscribe(self) -> None:
        unsubscribe = self._unsubscribe_voice
        self._unsubscribe_voice = None
        if unsubscribe is not None:
            unsubscribe()

    def unsubscribe_voice_assistant(self) -> None:
        self._unsubscribe()

    def subscribe_voice_assistant(
        self,
        *,
        handle_start: VoiceStartHandler,
        handle_stop: VoiceStopHandler,
        handle_audio: VoiceAudioHandler,
    ) -> None:
        """Register this device's voice callbacks and retain their unsubscriber."""
        if self.client is None:
            raise RuntimeError("Cannot subscribe voice callbacks before device connection")
        self._unsubscribe()
        self._unsubscribe_voice = self.client.subscribe_voice_assistant(
            handle_start=handle_start,
            handle_stop=handle_stop,
            handle_audio=handle_audio,
        )

    def update_settings(self, settings: ApiSettings) -> None:
        self.settings = settings
        if self.voice_client is not None:
            self.voice_client.update_settings(settings)


def _stable_device_id(mac_address: str | None, host: str) -> str:
    """Prefer the hardware address; use the host when unavailable."""
    mac = (mac_address or "").strip().lower()
    if mac:
        return mac
    return f"host:{host.strip().lower()}"
