# VAD diagnostics

The speaker can temporarily record the detector's decisions without recording
or retaining microphone audio. Set these API settings for a diagnostic run:

```text
ESPHOME_VAD_DIAGNOSTICS_ENABLED=true
ESPHOME_VAD_DIAGNOSTICS_DIR=/data/vad-diagnostics
```

The API writes one `vad-*.csv` file per voice capture. Each row is one 20 ms
frame. `webrtc_speech` is the binary WebRTC VAD decision (`0` or `1`; startup
grace frames are blank), `audio_level_dbfs` is the frame RMS level, `state` is
the endpointing state after that frame, and `silence_run` is the current
consecutive non-speech count. The files contain no audio and can be deleted
after review.

The directory should be on the API's persistent data volume if the files need
to survive a container restart. Keep diagnostics disabled outside a short
collection run because a long-running speaker will produce many rows.

## Plotting in Jupyter

```python
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

for path in sorted(Path("/data/vad-diagnostics").glob("vad-*.csv")):
    df = pd.read_csv(path)
    seconds = df["elapsed_ms"] / 1000
    speech = pd.to_numeric(df["webrtc_speech"], errors="coerce")

    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(14, 4),
                             gridspec_kw={"height_ratios": [1, 2]})
    axes[0].step(seconds, speech, where="post")
    axes[0].set_ylim(-0.1, 1.1)
    axes[0].set_ylabel("WebRTC VAD")
    axes[1].plot(seconds, df["audio_level_dbfs"])
    axes[1].set_ylabel("RMS (dBFS)")
    axes[1].set_xlabel("Seconds")
    fig.suptitle(path.name)
    plt.show()
```

Plotting several files side by side makes it easier to distinguish a mostly
quiet capture with isolated false positives from sustained speech-like audio.
Silero shadow scores can be added later without changing this CSV shape.
