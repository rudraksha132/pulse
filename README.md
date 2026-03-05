<div align="center">

# ⚡ ytdlp-ffmpeg-downloader

**The best YouTube/web video downloader — powered by yt-dlp & FFmpeg**

[![Python](https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python)](https://python.org)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-latest-red?style=flat-square)](https://github.com/yt-dlp/yt-dlp)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-required-green?style=flat-square&logo=ffmpeg)](https://ffmpeg.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow?style=flat-square)](LICENSE)

</div>

---

## ✨ Features

| Feature | Details |
|---|---|
| ⚡ **Best Quality Video** | `bestvideo+bestaudio` merged to MP4 via FFmpeg — 4K/8K capable |
| 🎵 **Audio Extraction** | MP3 · FLAC · AAC · WAV · OPUS · M4A at best VBR quality |
| 🖼 **Thumbnail Embedding** | Fetched from source and embedded into MP4/MP3 |
| 🏷 **Metadata Tagging** | Title, artist, year, URL written as ID3/MP4 tags |
| 📝 **Subtitles** | Download SRT, soft-embed into MP4, or burn-in (hardcode) |
| 🚫 **SponsorBlock** | Auto-cut sponsor segments, self-promos, and interactions |
| ✂ **Video Clipping** | Trim by timestamp via stream-copy (instant, lossless) |
| 📋 **Playlist Support** | Full playlist download with range selection |
| 🔁 **Retry Logic** | Exponential backoff — retries 3× on transient failures |
| 🎨 **Rich Terminal UI** | Live progress bars, spinners, panels, and tables |

---

## 📦 Requirements

- Python 3.11+
- [FFmpeg](https://ffmpeg.org/download.html) — must be in `PATH`

---

## 🚀 Installation

```bash
git clone https://github.com/rudraksha/ytdlp-ffmpeg-downloader.git
cd ytdlp-ffmpeg-downloader
pip install yt-dlp rich
```

> Downloads are saved to the `storage/` folder (auto-created, excluded from git).

---

## 🎬 Usage

```bash
python main.py
```

You'll be prompted to enter a URL (YouTube, Vimeo, Twitter, and [hundreds more](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)), then choose a download mode:

```
1 — ⚡ Best Quality Video   (4K/8K + best audio, merged)
2 — 🎵 Audio Only           (MP3 / FLAC / AAC / WAV / OPUS)
3 — 🛠  Custom Format        (pick exact stream from table)
4 — 📋 Playlist             (all or a range of videos)
```

After downloading, the tool optionally:
- ✂ Clips the video by start/end timestamp
- 🔤 Burns subtitles directly into the video
- 🏷 Writes metadata tags via FFmpeg

---

## ⚙️ Configuration

Edit the constants near the top of `main.py`:

```python
DOWNLOAD_DIR  = "storage"   # where files are saved
MAX_RETRIES   = 3           # retry attempts on failure
CONCURRENT_DL = 5           # parallel fragment downloads
```

---

## 📄 License

[MIT](LICENSE) © 2026 rudraksha
