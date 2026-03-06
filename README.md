<div align="center">

<br/>

```
██████╗ ██╗   ██╗██╗     ███████╗███████╗
██╔══██╗██║   ██║██║     ██╔════╝██╔════╝
██████╔╝██║   ██║██║     ███████╗█████╗  
██╔═══╝ ██║   ██║██║     ╚════██║██╔══╝  
██║     ╚██████╔╝███████╗███████║███████╗
╚═╝      ╚═════╝ ╚══════╝╚══════╝╚══════╝
```

**A fast, fully-configurable video & audio downloader.**  
*Powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp) · [FFmpeg](https://ffmpeg.org) · [Rich](https://github.com/Textualize/rich)*

<br/>

[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-latest-ff0000?style=flat-square&logo=youtube&logoColor=white)](https://github.com/yt-dlp/yt-dlp)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-required-007808?style=flat-square&logo=ffmpeg&logoColor=white)](https://ffmpeg.org)
[![License](https://img.shields.io/badge/License-MIT-f0db4f?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-555?style=flat-square)](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)

<br/>

</div>

---

## Overview

Pulse is a terminal-first downloader that wraps `yt-dlp` and `FFmpeg` into a clean, opinionated interface. Every quality parameter is configurable — resolution, codec, audio bitrate — with real-time size estimates before you commit to a download.

Works with **YouTube, Vimeo, Twitter/X, SoundCloud**, and [1000+ other sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).

---

## Quickstart

```bash
# 1. Clone
git clone https://github.com/rudraksha132/pulse.git && cd pulse

# 2. Install Python dependencies
pip install yt-dlp rich

# 3. Run
python main.py
```

> [!IMPORTANT]  
> **FFmpeg must be installed and available in your `PATH`.**  
> Without it, merging, audio extraction, thumbnail embedding, and subtitle burn-in are unavailable.  
> → [Download FFmpeg](https://ffmpeg.org/download.html)

---

## Features

<details>
<summary><b>⚡ Video Download</b> — every resolution, 144p to 8K</summary>

<br/>

Choose from 11 quality presets, with **estimated file sizes** calculated from the video's duration before you download:

| # | Quality | Typical Bitrate |
|---|---------|----------------|
| 1 | Best Available (up to 8K) | ~25,000 kbps |
| 2 | 8K · 4320p | ~25,000 kbps |
| 3 | 4K · 2160p | ~12,000 kbps |
| 4 | 1440p · 2K | ~6,000 kbps |
| 5 | 1080p · FHD | ~2,500 kbps |
| 6 | 720p · HD | ~1,200 kbps |
| 7 | 480p · SD | ~700 kbps |
| 8–11 | 360p / 240p / 144p / Worst | ~400–100 kbps |

Video streams are merged with the best available audio via FFmpeg into an **MKV container** (supports AV1, VP9, H.265, H.264), then losslessly remuxed to **MP4** — no re-encoding, no quality loss.

</details>

<details>
<summary><b>🎵 Audio Extraction</b> — 6 formats × 8 quality levels</summary>

<br/>

**Formats:** `MP3` · `AAC` · `FLAC` · `WAV` · `OPUS` · `M4A`

For lossy formats, you pick the exact quality — with estimated output sizes shown upfront:

| Quality | Setting |
|---------|---------|
| Best (VBR) | `0` — variable bitrate, best possible |
| 320 kbps | CBR, CD-quality lossy |
| 256 / 192 / 128 / 96 / 64 kbps | Progressive quality levels |
| Worst (~32 kbps) | Minimum size |

FLAC and WAV always encode losslessly regardless of the quality selector.

</details>

<details>
<summary><b>🛠 Advanced Features</b></summary>

<br/>

| Feature | How it works |
|---------|-------------|
| **Custom Format Picker** | Inspect every available stream in a formatted table (ID, ext, resolution, FPS, codec, size, note) and download any combination |
| **Playlist Download** | Download entire playlists or a custom index range, with per-quality selection |
| **SponsorBlock** | Automatically removes sponsor segments, self-promos, and interaction reminders |
| **Subtitle Support** | Download `.srt` files, soft-embed into MP4, or hard-burn directly into the video |
| **Thumbnail Embedding** | Source thumbnail fetched and embedded into the output file (MP4 / MKV / MP3) |
| **Metadata Tagging** | Title, artist/uploader, upload year, and source URL written as ID3/MP4 tags |
| **Video Clipping** | Trim any section by start/end timestamp (`HH:MM:SS`) via lossless stream-copy |
| **Retry Logic** | Exponential backoff — retries up to 3× on transient network failures |
| **Rich Terminal UI** | Live progress bars, download speed, ETA, spinners, and formatted tables |

</details>

---

## How It Works

```
Enter URL
    └─▶  Fetch metadata (title, duration, formats)
              └─▶  Select mode
                        ├─▶  [1] Video → pick resolution → download → remux MKV→MP4
                        ├─▶  [2] Audio → pick format + bitrate → extract
                        ├─▶  [3] Custom → inspect streams → pick format ID → download
                        └─▶  [4] Playlist → pick range + resolution → batch download

Post-processing (optional)
    ├─▶  Embed thumbnail
    ├─▶  Write metadata tags
    ├─▶  Clip by timestamp
    └─▶  Burn subtitles
```

---

## Configuration

A handful of constants at the top of `main.py` control global behaviour:

```python
DOWNLOAD_DIR   = "storage"   # output folder (auto-created)
MAX_RETRIES    = 3           # retry attempts on network failure
RETRY_DELAY    = 2           # initial delay in seconds (doubles each retry)
CONCURRENT_DL  = 5           # parallel fragment downloads (HLS/DASH)
```

---

## Requirements

| Dependency | Version | Install |
|-----------|---------|---------|
| Python | 3.11+ | [python.org](https://python.org) |
| yt-dlp | latest | `pip install yt-dlp` |
| rich | latest | `pip install rich` |
| FFmpeg | any | [ffmpeg.org](https://ffmpeg.org/download.html) ← must be in PATH |

---

<div align="center">

[MIT License](LICENSE) · Built by [rudraksha](https://github.com/rudraksha132)

</div>
