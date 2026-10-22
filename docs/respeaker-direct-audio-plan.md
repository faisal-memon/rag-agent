# Direct ReSpeaker Audio Streaming Plan

## Goal

Send push-to-talk audio from the ReSpeaker Lite's XIAO ESP32-S3 directly to the
RAG service on the 3090, without Home Assistant in the audio path. Keep the
device transport stable so the server can later add transcription, agent
responses, and spoken playback without replacing the initial streaming work.

The firmware work starts from the [ReSpeaker Lite ESPHome integration fork](https://github.com/faisal-memon/Respeaker-Lite-ESPHome-integration).
The RAG service currently exposes `POST /agent/transcribe`, which uploads a
complete recording to the existing local `whisper.cpp` HTTP endpoint. It does
not yet expose a device voice session or WebSocket endpoint.

## First milestone: prove audio transport without the board

Implement the WebSocket receiver in the RAG API and a small Python simulator
before writing or flashing device firmware. The simulator reads a short
recording, converts it to the agreed PCM format, and sends the same messages
and audio frames the ESP32 will send.

The receiver is `GET`-style WebSocket endpoint `/agent/voice/stream`; the
simulator is `tools/voice_stream_simulator.py`. It deliberately accepts only
an already-converted 16 kHz, mono, 16-bit PCM WAV. Conversion belongs outside
the protocol test so that format mismatches are visible rather than hidden.

### Draft stream contract

- Connect to one configured RAG API WebSocket URL.
- Send a JSON `start` message specifying 16,000 Hz, one channel, signed
  16-bit little-endian PCM, and a protocol version.
- Send bounded binary PCM frames.
- Send a JSON `end` message to finish the utterance.
- Receive a JSON acknowledgement with accepted sample count and duration, or a
  clear error code for malformed, oversized, or overlong input.
- Set a maximum utterance length and total byte limit on the server.

Keep frame handling independent of transcription. The server can assemble the
frames into a temporary WAV for validation. Automated tests should inspect
the WAV header, sample count, duration, and audio payload, then verify cleanup.
A development-only capture option can retain one short sample long enough for
manual playback; it must default off and must never be enabled on a public
endpoint.

### Network exposure for the test

For the first hardware-free test, allow an unauthenticated connection only
from the private home network. Do not add a public SWAG route. Publish or
forward the API port only on the host's LAN interface and restrict it with the
host firewall where available. Keep the test endpoint disabled or unbound
outside that test setup. Add authentication before connecting it to agent
capabilities or private data.

## Success criteria

The hardware-free milestone passes when:

1. The simulator can stream a known short recording to the API.
2. The API acknowledges the correct sample count and plausible duration.
3. The reconstructed WAV has the agreed format and matches the submitted PCM
   payload; a development capture plays clearly when manual audio QA is used.
4. Invalid formats, disconnected clients, oversized streams, and duration
   limits produce bounded failures and leave no stale temporary files.
5. The endpoint is reachable on the private LAN and is not exposed through the
   public proxy.

This verifies the server protocol and audio reconstruction, but not the
ReSpeaker microphone, I²S wiring, ESPHome component, or Wi-Fi behavior.

## Implementation phases

1. **Server protocol and simulator:** Add a versioned WebSocket route, bounded
   PCM assembly, temporary WAV handling, acknowledgements, cleanup, and
   automated tests. Keep agent and transcription calls out of this phase.
2. **Board firmware:** In the fork, retain ESPHome's ReSpeaker configuration,
   I²S microphone path, mute/button handling, and LEDs. Add a C++ component for
   push-to-talk capture and the same WebSocket contract. Put the server URL
   in one editable firmware setting. Test locally with the simulator before
   flashing.
3. **Hardware transport test:** Flash the XIAO ESP32-S3 with the ReSpeaker's
   I²S firmware as required, speak a short phrase, and verify server-reported
   duration and captured audio quality.
4. **Speech and agent integration:** Feed completed recordings through the
   existing local `whisper.cpp` client, then pass the transcript into the RAG
   agent. Define device identity and which user/private tools it may access
   before enabling this phase; a device credential alone does not identify
   the person speaking.
5. **Reply audio and production hardening:** Add Piper playback, device
   enrollment, authenticated TLS, reconnect behavior, and firmware updates
   after the basic round trip works.

## Open decisions before implementation

- Confirm the ESP32's I²S sample format from the selected ReSpeaker/XMOS
  firmware; convert to the protocol's 16 kHz mono PCM at capture if needed.
- Decide how the API port will be reachable only from the home LAN while
  remaining outside the public proxy.
- Decide how a speaker is associated with a person before granting access to
  personal memory, documents, or private tools.
- Decide whether the first server test keeps a short development capture for
  manual playback, or relies on automated payload and WAV validation alone.
