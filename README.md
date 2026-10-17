# Wordcut

A small self-hosted web app for cutting YouTube clips or your own audio/video
files precisely, using a zoomable waveform with draggable start/end markers.
It can also convert/export to common video and audio formats and extract
YouTube caption transcripts.

The server is plain Python (standard library only) plus static HTML/CSS/JS; media
work is done by FFmpeg and yt-dlp. There is no frontend build step and no CDN or
network dependency in the page.

## Usage

1. Open **Source & initial selection**, paste a YouTube video link, and enter start/end times. Seconds, M:SS and H:MM:SS accept up to three decimal places.
2. Prepare clip. The server downloads the requested interval with one extra second on each side, clamped to the video's beginning/end. For 0:00–0:28, this is 0:00–0:29.
3. Drag IN and OUT onto quiet gaps in the waveform. Use zoom and the pan slider. Focus start/end zooms to a two-second neighborhood. All displayed cut times refer to the original YouTube timeline.
4. Use 1 ms / 10 ms nudges, or focus a marker and press arrow keys (Shift = 10 ms). Hear around start/end plays 0.7 seconds on either side where available. Play selection auditions only the selected audio. Playback supports 0.5× and 0.75×.
5. Choose video or audio-only export, set the format, then export and download the result. Adjustable fade-in and fade-out (0–2000 ms, default 3 ms) reduce clicks; long fades are capped at half the selection. Audio is trimmed at sample precision using the entered millisecond boundaries; video boundaries follow source frames, not arbitrary milliseconds. MP4/MOV/MKV use H.264 + AAC; WebM uses VP9 + Opus. MP4 is the default for broad playback compatibility.

Reloading or opening from another browser lists recent clips. Changed markers are persisted when exported; pending edits are local to the current page.

## Duration targeting

Start, Duration and End are linked. Choose an anchor:

- **From start**: End = Start + Duration. Editing Start shifts the whole range.
- **Up to end**: Start = End − Duration, for a fixed amount of lead-up to a moment.
  Editing End shifts the whole range.

Type durations as `30`, `45s`, `1m 30s` or `1:30`. To adjust a duration, use
↑/↓ (Shift ±0.1 s, Alt ±10 s), drag the ⟷ handle sideways, or drag the rail. The
rail uses a square-root scale up to the 10-minute limit; its tick labels are
presets. In the editor, **Length** works the same way for IN/OUT (keep IN or keep OUT),
limited to the prepared window. Marker drags and nudges still move only one edge.
Durations that do not fit are clamped, with a short note explaining why.

## Conversion and export options

Upload MP3, M4A, WAV, FLAC, Ogg, Opus, AAC, AIFF, MP4, MOV, MKV or WebM through **Source & initial selection**. Files are streamed to disk, limited to 250 MiB and 10 minutes. Audio-only sources show an audio player and automatically select audio export. The full uploaded file is available to trim; YouTube downloads retain the one-second editing handles.

- Video: MP4, MOV, MKV, WebM; high/balanced/small quality; preview size or maximum 720p/480p/360p; optional silent video. No upscaling.
- Audio-only: MP3, M4A/AAC, WAV (16-bit PCM), FLAC, Ogg/Vorbis, Opus.
- Fine details: bitrate (64–320 kbps where applicable), 16/22.05/44.1/48 kHz, source/mono/stereo channels, −30 to +12 dB volume, separate fades, custom download filename and actual exported file size.
- Vorbis uses variable bitrate driven by the quality setting. WAV/FLAC disable bitrate. MP3 at 16/22.05 kHz is capped at 128 kbps. Opus permits 16 or 48 kHz; WebM's audio uses 48 kHz.
- Selection playback previews volume and fades. Sample-rate, channel and codec conversion apply on export. Audio-only exports contain no video stream. Download links are invalidated in the UI when selection/settings change, so an earlier conversion is not misrepresented.
- Imports and preparations preserve decoded audio in an internal FLAC master. Lossless outputs preserve the available audio; they do not recover detail lost in compressed sources.

## Transcripts

Open a prepared YouTube clip and click **Extract transcript** above the export
settings.

- Scroll through the text and highlight any passage. **Copy highlighted text**
  copies just that selection; Cmd+C / Ctrl+C also works. **Copy shown text** and
  **Download .txt** use the current scope, search and timestamp settings.
- Switch between **Current clip selection** and **Full video**. Clip scope follows
  the current IN/OUT markers and includes complete caption lines overlapping that
  interval. Caption timing is approximate; use the waveform for precise audio cuts.
- Choose an available caption language, filter lines with Search, or include original
  video timestamps. Automatic captions and translations may contain errors.
- Extraction uses existing creator or automatic YouTube captions through
  [yt-dlp's subtitle options](https://github.com/yt-dlp/yt-dlp#subtitle-options).
  It does not download the full video or load an AI model. Files without a YouTube
  source and videos without available captions are reported as unsupported;
  there is no local speech-recognition fallback.
- Caption processing uses the same single worker, cancel button, resource limits
  and seven-day storage policy. Failure or cancellation preserves the prepared clip
  and any previous transcript/export. Source metadata and temporary caption files
  are removed; only sanitized text, timing and language labels are served to the UI.

## Install

Requirements: Linux (or another POSIX system), Python 3 (tested with 3.12), FFmpeg/ffprobe, and
Node.js (used by yt-dlp to solve YouTube's player JavaScript; developed with Node 24).

```sh
git clone https://github.com/jraw95/wordcut.git ~/wordcut
cd ~/wordcut
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
WORDCUT_NODE="$(command -v node)" .venv/bin/python server.py
```

Then open http://127.0.0.1:18447/. yt-dlp is used from `.venv` and is pinned in
`requirements.txt`; YouTube changes often, so update it when downloads start failing.

### Configuration

All settings are environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `WORDCUT_PORT` | `18447` | Listening port. |
| `WORDCUT_STATE` | `~/.local/state/wordcut` | Clip and job storage (`jobs/` inside it). |
| `WORDCUT_LAN_IP` | unset | Also listen on this LAN address. Requires `WORDCUT_LAN_NETWORK`. |
| `WORDCUT_LAN_NETWORK` | unset | Client subnet allowed to connect over the LAN, e.g. `192.168.1.0/24`. |
| `WORDCUT_NODE` | `~/.local/bin/node` | Node.js executable for yt-dlp. |
| `WORDCUT_FFMPEG` / `WORDCUT_FFPROBE` | `/usr/bin/ffmpeg` / `/usr/bin/ffprobe` | FFmpeg executables. |

Without the LAN settings, Wordcut listens on `127.0.0.1` only. It never binds all
interfaces.

### Running as a user service

`wordcut.service` is a systemd user unit with resource limits and sandboxing. Copy it
to `~/.config/systemd/user/`, adjust paths and the commented LAN settings, then run
`systemctl --user daemon-reload && systemctl --user start wordcut.service`.
`launcher.sh` (installed as `~/.local/bin/wordcut`) and the two `.desktop` files
provide start/stop desktop shortcuts.

## Resource bounds and security model

- CPU-only processing; no GPU/model loading. One media operation at a time; cancel terminates its process group.
- Maximum selected duration: 10 minutes. Download/preview/export capped at 720p with two encoder threads. The sample unit adds CPUQuota=200%, MemoryMax=1G, MemoryHigh=768M and MemorySwapMax=0, and makes the home directory read-only except for the state directory.
- Process timeouts, 2 GiB per-file resource limit, 1 GiB downloader filesize filter, 15-minute upload deadline, at least 5 GiB free disk required. Before each new download, clips older than seven days are removed and the oldest clips are pruned when the cache reaches 10 GiB. Only app-owned clips are touched.
- **There is no login.** Access control is by network position: loopback, plus the configured LAN subnet, with Host-header allowlisting (against DNS rebinding) and same-origin plus custom-header checks on every mutation. Anyone who can reach the port can see and download its clips and start jobs, so only enable LAN access on a network you trust, and never expose the port to the internet.
- Only canonical single-video YouTube URLs are accepted. No arbitrary local paths or shell commands; subprocesses are run without a shell. Job IDs and media filenames are allowlisted. Uploaded media is decoded with a restricted FFmpeg protocol/demuxer list. Safari-compatible HTTP byte ranges and HEAD are supported.
- YouTube can refuse private, restricted or unauthenticated requests; errors are shown in the UI. No YouTube login or cookies are used.

Use Wordcut only for content you have the right to download and edit, and in line
with YouTube's Terms of Service and applicable copyright law.

References: [yt-dlp section downloads](https://github.com/yt-dlp/yt-dlp#usage-and-options), [FFmpeg trim/atrim filters](https://ffmpeg.org/ffmpeg-filters.html#atrim).

## Tests

```sh
.venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
```

The tests cover timestamp parsing, padding both boundaries, selection constraints, YouTube URL canonicalization/allowlisting, invalid cut positions, Safari byte ranges/HEAD, cross-origin/Host rejection, traversal, concurrency, cleanup robustness, subprocess cancellation, a real FFmpeg export across a known audio-frequency transition, every output encoder, audio-only imports/master retention, silent video, format/rate validation, fade bounds, and caption parsing, selection, cleanup and failure isolation. They do not contact YouTube.

## License

MIT — see [LICENSE](LICENSE).
