# Speaker device identity and history

## First increment

- Identify the configured ESPHome speaker by its MAC address, with its host as a fallback.
- Store the device identity, display name, connection address, last connection time, and last interaction time in Postgres.
- Store spoken transcripts and assistant replies in a shared history keyed by device ID. Keep the latest 12 messages as model context and show up to 100 messages in the Devices page.
- Add an authenticated Devices page showing current connection status and saved history. Connection status means the Native API link is up; it does not verify wake-word, microphone, or speaker playback.
- Keep device identity separate from the signed-in web user's identity. The speaker does not identify who is speaking.

## Later: add more devices

The current service creates one ESPHome client from one set of environment settings. Supporting an Add device flow needs a device registry, per-device connection lifecycle and a secure way to store each device's API key. Keep that separate from the first read-only management page so device credentials are not put into ordinary runtime settings or returned to the browser.
