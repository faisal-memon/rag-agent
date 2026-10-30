# ReSpeaker through the ESPHome Native API

The ReSpeaker satellite firmware exposes ESPHome's Native API on TCP port
6053. The API service connects to the device and subscribes to its voice
assistant events. The firmware handles the USR button and microphone; the API
client receives the resulting session and audio frames.

The first milestone in `app/agent/esphome_voice.py` is deliberately limited to
the connection and audio transport. It logs session starts, frame totals, and
session ends, keeps at most 3 MB of one session in memory, and then discards the
audio. It does not yet transcribe audio or run the RAG agent.

Configure the API service with:

- `ESPHOME_DEVICE_HOST`: the device's reserved LAN address or reachable host
- `ESPHOME_DEVICE_PORT`: Native API port; defaults to `6053`
- `ESPHOME_DEVICE_NAME`: optional expected ESPHome node name, used to detect an
  address that has been reassigned
- `ESPHOME_API_KEY`: optional ESPHome Noise encryption key

When `ESPHOME_DEVICE_HOST` is empty, the client stays disabled. The service
reconnects when the device is temporarily unavailable and stops its connection
cleanly with the API application.
