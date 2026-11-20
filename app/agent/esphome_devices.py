"""Manage an ESPHome device's Native API connection and voice subscription."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import aioesphomeapi

from app.agent import voice_devices
from app.agent.config import ApiSettings

log = logging.getLogger(__name__)

VoiceStartHandler = Callable[..., Awaitable[int]]
VoiceStopHandler = Callable[..., Awaitable[None]]
VoiceAudioHandler = Callable[..., Awaitable[None]]
DeviceReadyHandler = Callable[[str | None, str, int | None], Awaitable[None]]
DeviceDisconnectedHandler = Callable[[bool], Awaitable[None]]


class ESPHomeDevice:
    """Own one device connection, its identity, and its voice callbacks."""

    def __init__(
        self,
        settings: ApiSettings,
        *,
        handle_start: VoiceStartHandler,
        handle_stop: VoiceStopHandler,
        handle_audio: VoiceAudioHandler,
        on_ready: DeviceReadyHandler,
        on_disconnect: DeviceDisconnectedHandler,
    ) -> None:
        self.settings = settings
        self._handle_start = handle_start
        self._handle_stop = handle_stop
        self._handle_audio = handle_audio
        self._on_ready = on_ready
        self._on_disconnected = on_disconnect
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

        # Subscribe this device's voice callbacks and retain the unsubscriber.
        self._unsubscribe_voice = self.client.subscribe_voice_assistant(
            handle_start=self._handle_start,
            handle_stop=self._handle_stop,
            handle_audio=self._handle_audio,
        )

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

        log.info(
            "Connected to ESPHome device %s (%s); Native API audio subscribed",
            info.name,
            info.mac_address,
        )
        await self._on_ready(self.device_id, self.display_name, self.media_player_key)

    async def _on_disconnect(self, expected: bool) -> None:
        self.connected = False
        self._unsubscribe()
        await self._on_disconnected(expected)

    async def _on_connect_error(self, error: Exception) -> None:
        log.warning("Could not connect to ESPHome device: %s", error)

    def _unsubscribe(self) -> None:
        unsubscribe = self._unsubscribe_voice
        self._unsubscribe_voice = None
        if unsubscribe is not None:
            unsubscribe()


def _stable_device_id(mac_address: str | None, host: str) -> str:
    """Prefer the hardware address; use the host when unavailable."""
    mac = (mac_address or "").strip().lower()
    if mac:
        return mac
    return f"host:{host.strip().lower()}"
