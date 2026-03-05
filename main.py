"""
╔═══════════════════════════════════════════════════════╗
║          YT-DLP + FFMPEG SUPER DOWNLOADER             ║
║   Best quality · Audio rip · Subtitles · Thumbnails   ║
╚═══════════════════════════════════════════════════════╝

Requirements:
    pip install yt-dlp rich
    FFmpeg must be in PATH (https://ffmpeg.org/download.html)

Features:
  • Download best video (4K/8K when available, merged via FFmpeg)
  • Audio-only extraction: MP3, AAC, FLAC, WAV, OPUS
  • Custom format selection with smart audio-merge detection
  • Subtitle download + optional FFmpeg burn-in (hardcode)
  • Embed thumbnail into MP4/MKV/MP3
  • Full metadata tags via FFmpeg post-processor
  • Playlist support with per-item + overall progress
  • SponsorBlock chapter markers / skip integration
  • Clip trimming by start/end timestamp (FFmpeg remux)
  • Concurrent fragment downloads (faster HLS/DASH)
  • Exponential-backoff retry on transient failures
  • Rich terminal UI with live panels and tables
"""

import json
import os
import re
import sys
import shutil
import subprocess
import time
import yt_dlp

from rich import print as rprint
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn,
    TimeElapsedColumn, TimeRemainingColumn, DownloadColumn,
    TransferSpeedColumn, TaskProgressColumn,
)
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.text import Text
from rich.rule import Rule
from rich.columns import Columns
from rich import box

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
DOWNLOAD_DIR   = "storage"
MAX_RETRIES    = 3
RETRY_DELAY    = 2          # seconds (doubles each attempt)
FFMPEG_PATH    = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_PATH   = shutil.which("ffprobe") or "ffprobe"

AUDIO_FORMATS  = {"mp3": "mp3", "aac": "aac", "flac": "flac",
                  "wav": "wav", "opus": "opus", "m4a": "m4a"}

console = Console()


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def aria2c_available() -> bool:
    return shutil.which("aria2c") is not None


def format_bytes(size: float | None) -> str:
    if not size:
        return "N/A"
    for unit in ("", "K", "M", "G", "T"):
        if size < 1024:
            return f"{size:.1f} {unit}B"
        size /= 1024
    return f"{size:.1f} PB"


def parse_timestamp(ts: str) -> float:
    """Convert HH:MM:SS or MM:SS or seconds string to float seconds."""
    parts = ts.strip().split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def print_header() -> None:
    console.print(Panel.fit(
        "[bold cyan]YT-DLP[/bold cyan] [white]+[/white] [bold magenta]FFmpeg[/bold magenta] "
        "[dim]Super Downloader[/dim]",
        border_style="bright_blue",
        padding=(0, 4),
    ))
    if not ffmpeg_available():
        console.print(Panel(
            "[bold yellow]⚠  FFmpeg not found in PATH![/bold yellow]\n"
            "[dim]Some features (merging, audio extraction, subtitle burn-in,\n"
            "thumbnail embedding) will be [bold red]unavailable[/bold red].\n"
            "Install FFmpeg → [link=https://ffmpeg.org/download.html]ffmpeg.org[/link][/dim]",
            border_style="yellow",
        ))
    else:
        console.print(f"[dim]FFmpeg:[/dim] [green]{FFMPEG_PATH}[/green]  "
                      f"[dim]Output:[/dim] [cyan]{os.path.abspath(DOWNLOAD_DIR)}[/cyan]")

    # Show active download engine
    if aria2c_available():
        console.print("[dim]Engine:[/dim] [bold green]aria2c ⚡[/bold green] "
                      "[dim](16 parallel connections per file)[/dim]")
    else:
        console.print("[dim]Engine:[/dim] [blue]native concurrent[/blue] "
                      "[dim](16 parallel fragment downloads)[/dim]")
    console.print()


# ─────────────────────────────────────────────
# FFMPEG POST-PROCESSING UTILITIES
# ─────────────────────────────────────────────
def run_ffmpeg(*args: str, show_output: bool = False) -> subprocess.CompletedProcess:
    """Run FFmpeg with optional stderr suppression."""
    cmd = [FFMPEG_PATH, "-y"] + list(args)
    if not show_output:
        return subprocess.run(cmd, capture_output=True, text=True)
    return subprocess.run(cmd)


def clip_video(src: str, start: str, end: str, out: str) -> str:
    """
    Trim/clip a video between start and end timestamps using stream-copy
    (fast, lossless) and fall back to re-encoding if copy fails.
    Returns output path.
    """
    console.print(f"[cyan]✂  Clipping[/cyan] {start} → {end} …")
    result = run_ffmpeg(
        "-ss", start, "-to", end,
        "-i", src,
        "-c", "copy",          # lossless stream copy
        "-avoid_negative_ts", "make_zero",
        out,
    )
    if result.returncode != 0:
        # Fallback: re-encode (handles non-keyframe start points)
        console.print("[yellow]⚠  Stream-copy failed; re-encoding clip (slower)…[/yellow]")
        run_ffmpeg(
            "-ss", start, "-to", end,
            "-i", src,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
            out,
        )
    return out


def burn_subtitles(video: str, subtitle: str, out: str) -> str:
    """Burn (hardcode) an SRT/VTT subtitle file into the video via FFmpeg."""
    console.print(f"[cyan]🔤 Burning subtitles:[/cyan] {os.path.basename(subtitle)}")
    # FFmpeg subtitles filter requires the path escaped for Windows
    safe_sub = subtitle.replace("\\", "/").replace(":", "\\:")
    run_ffmpeg(
        "-i", video,
        "-vf", f"subtitles='{safe_sub}':force_style='FontSize=28,PrimaryColour=&H00FFFFFF'",
        "-c:a", "copy",
        out,
    )
    return out


def embed_thumbnail(video: str, thumbnail: str) -> None:
    """Embed a JPEG thumbnail into an MP4 or MKV using FFmpeg."""
    tmp = video + ".tmp" + os.path.splitext(video)[1]
    result = run_ffmpeg(
        "-i", video,
        "-i", thumbnail,
        "-map", "0", "-map", "1",
        "-c", "copy",
        "-disposition:v:1", "attached_pic",
        tmp,
    )
    if result.returncode == 0 and os.path.exists(tmp):
        os.replace(tmp, video)
    elif os.path.exists(tmp):
        os.remove(tmp)


def add_metadata(video: str, title: str = "", artist: str = "",
                 album: str = "", year: str = "", comment: str = "") -> None:
    """Write ID3/MP4 metadata tags into a file using FFmpeg."""
    tmp = video + ".meta" + os.path.splitext(video)[1]
    meta_args = []
    for key, val in (("title", title), ("artist", artist),
                     ("album", album), ("date", year), ("comment", comment)):
        if val:
            meta_args += ["-metadata", f"{key}={val}"]
    if not meta_args:
        return
    result = run_ffmpeg("-i", video, "-c", "copy", *meta_args, tmp)
    if result.returncode == 0 and os.path.exists(tmp):
        os.replace(tmp, video)
    elif os.path.exists(tmp):
        os.remove(tmp)


def verify_download(filepath: str, expected_duration: int = 0) -> None:
    """Run ffprobe on the downloaded file and display quality details."""
    if not shutil.which("ffprobe"):
        return

    try:
        result = subprocess.run(
            [FFPROBE_PATH, "-v", "quiet", "-print_format", "json", "-show_streams",
             "-show_format", filepath],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return

        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        fmt_info = data.get("format", {})

        video_lines: list[str] = []
        audio_lines: list[str] = []

        for s in streams:
            codec_type = s.get("codec_type")
            if codec_type == "video" and s.get("disposition", {}).get("attached_pic") != 1:
                w = s.get("width", "?")
                h = s.get("height", "?")
                codec = s.get("codec_name", "?")
                fps_r = s.get("r_frame_rate", "0/1")
                try:
                    num, den = fps_r.split("/")
                    fps = round(int(num) / int(den))
                except (ValueError, ZeroDivisionError):
                    fps = "?"
                bitrate = s.get("bit_rate")
                br_str = f"{int(bitrate) // 1000} kbps" if bitrate else "N/A"
                pix_fmt = s.get("pix_fmt", "")
                hdr = "HDR" if any(x in pix_fmt for x in ("p010", "yuv420p10")) else "SDR"
                video_lines.append(
                    f"  🎬 [bold]{w}×{h}[/bold] [cyan]{codec}[/cyan] "
                    f"[dim]{fps}fps · {br_str} · {hdr}[/dim]"
                )
            elif codec_type == "audio":
                codec = s.get("codec_name", "?")
                sr = s.get("sample_rate", "?")
                ch = s.get("channels", "?")
                bitrate = s.get("bit_rate")
                br_str = f"{int(bitrate) // 1000} kbps" if bitrate else "N/A"
                audio_lines.append(
                    f"  🔊 [cyan]{codec}[/cyan] [dim]{br_str} · {sr}Hz · {ch}ch[/dim]"
                )

        # Duration check
        duration_str = ""
        file_dur = float(fmt_info.get("duration", 0))
        if expected_duration and file_dur:
            diff = abs(file_dur - expected_duration)
            if diff < 2:
                duration_str = f"  ⏱ [green]Duration OK[/green] [dim]({file_dur:.0f}s)[/dim]"
            else:
                duration_str = (f"  ⏱ [yellow]Duration mismatch:[/yellow] "
                                f"expected {expected_duration}s, got {file_dur:.0f}s")

        # File size
        try:
            size = os.path.getsize(filepath)
            size_str = format_bytes(size)
        except OSError:
            size_str = "N/A"

        lines = ["[bold white]📊 Quality Verification[/bold white]"]
        lines.extend(video_lines)
        lines.extend(audio_lines)
        if duration_str:
            lines.append(duration_str)
        lines.append(f"  💾 [dim]File size: {size_str}[/dim]")

        console.print(Panel("\n".join(lines), border_style="bright_blue"))

    except Exception:
        pass  # Non-critical — don't break the flow



# ─────────────────────────────────────────────
# PROGRESS TRACKING (live Rich progress bar)
# ─────────────────────────────────────────────
class RichProgress:
    """Wraps yt-dlp progress hooks into a Rich live progress display."""

    def __init__(self):
        self.progress: Progress | None = None
        self.task_id = None
        self._live = None

    def start(self) -> "RichProgress":
        self.progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=36, style="cyan", complete_style="green"),
            TaskProgressColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        )
        self.progress.start()
        return self

    def stop(self) -> None:
        if self.progress:
            self.progress.stop()

    def hook(self, d: dict) -> None:
        """yt-dlp progress hook compatible with Rich."""
        if not self.progress:
            return

        status = d.get("status")

        if status == "downloading":
            total   = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            current = d.get("downloaded_bytes", 0)
            fname   = os.path.basename(d.get("filename", "…"))[:40]

            if self.task_id is None:
                self.task_id = self.progress.add_task(
                    f"[cyan]{fname}", total=total or 100
                )
            else:
                self.progress.update(
                    self.task_id,
                    description=f"[cyan]{fname}",
                    completed=current,
                    total=total if total else None,
                )

        elif status == "finished":
            if self.task_id is not None:
                self.progress.update(self.task_id, completed=100, total=100)
            self.task_id = None

        elif status == "error":
            if self.task_id is not None:
                self.progress.stop_task(self.task_id)
            self.task_id = None


# ─────────────────────────────────────────────
# CORE DOWNLOADER
# ─────────────────────────────────────────────
class SuperDownloader:
    def __init__(self):
        ensure_dir(DOWNLOAD_DIR)
        self.rich_progress = RichProgress()

    # ── Metadata ──────────────────────────────
    def get_info(self, url: str) -> dict | None:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extractor_args": {"youtube": {"player_client": ["android", "ios", "web"]}},
        }
        with console.status("[cyan]Fetching video info…", spinner="dots"):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)
            except Exception as e:
                console.print(f"[red]❌ Error fetching info:[/red] {e}")
                return None

    def display_info(self, info: dict) -> None:
        is_playlist = info.get("_type") == "playlist"
        if is_playlist:
            n = len(info.get("entries", []))
            console.print(Panel(
                f"[bold white]📋 Playlist:[/bold white] [cyan]{info.get('title','Unknown')}[/cyan]\n"
                f"[dim]{n} video(s)[/dim]",
                border_style="blue",
            ))
        else:
            dur = info.get("duration", 0)
            h, m, s = dur // 3600, (dur % 3600) // 60, dur % 60
            duration_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"
            uploader = info.get("uploader") or info.get("channel") or "Unknown"
            console.print(Panel(
                f"[bold white]🎬 {info.get('title', 'Unknown')}[/bold white]\n"
                f"[dim]👤 {uploader}  •  ⏱ {duration_str}  •  "
                f"👁 {info.get('view_count', 0):,} views[/dim]",
                border_style="cyan",
            ))

    # ── Format Table ──────────────────────────
    def list_formats(self, info: dict) -> list[str]:
        formats = info.get("formats", [])
        table = Table(
            title="Available Formats",
            box=box.ROUNDED,
            header_style="bold magenta",
            border_style="bright_blue",
            show_lines=False,
        )
        table.add_column("ID",       style="bold yellow",  min_width=8)
        table.add_column("EXT",      style="cyan",         min_width=5)
        table.add_column("RES",      style="green",        min_width=10)
        table.add_column("FPS",      style="dim",          min_width=4)
        table.add_column("CODEC",    style="dim",          min_width=10)
        table.add_column("SIZE",     style="blue",         min_width=9)
        table.add_column("NOTE",     style="white")

        valid_ids: list[str] = []
        for f in formats:
            if f.get("protocol") in ("m3u8_native", "m3u8"):
                continue
            f_id   = str(f.get("format_id", ""))
            ext    = f.get("ext", "")
            res    = f.get("resolution") or ("audio only" if f.get("acodec") != "none" and f.get("vcodec") == "none" else "N/A")
            fps    = str(f.get("fps") or "")
            vcodec = f.get("vcodec", "")
            acodec = f.get("acodec", "")
            codec  = "/".join(filter(lambda x: x and x != "none", [vcodec, acodec]))[:16]
            size   = format_bytes(f.get("filesize") or f.get("filesize_approx"))
            note   = f.get("format_note", "")

            table.add_row(f_id, ext, res, fps, codec, size, note)
            valid_ids.append(f_id)

        console.print(table)
        return valid_ids

    # ── Download Core ─────────────────────────
    def _build_ydl_opts(
        self,
        fmt: str,
        audio_only: bool = False,
        audio_fmt: str = "mp3",
        embed_thumb: bool = True,
        embed_subs: bool = False,
        write_subs: bool = False,
        sub_langs: str = "en",
        sponsorblock: bool = False,
        extra_postprocessors: list | None = None,
    ) -> dict:
        """Build yt_dlp options dict."""
        outtmpl = f"{DOWNLOAD_DIR}/%(title)s.%(ext)s"

        postprocessors: list[dict] = []

        if audio_only and ffmpeg_available():
            postprocessors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_fmt,
                "preferredquality": "0",   # best (VBR for MP3, lossless for FLAC)
            })

        if embed_thumb and ffmpeg_available():
            postprocessors.append({"key": "EmbedThumbnail"})

        if (embed_subs or write_subs) and ffmpeg_available():
            postprocessors.append({
                "key": "FFmpegSubtitlesConvertor",
                "format": "srt",
            })

        if sponsorblock and ffmpeg_available():
            postprocessors.append({
                "key": "SponsorBlock",
                "categories": ["sponsor", "selfpromo", "interaction"],
            })
            postprocessors.append({
                "key": "ModifyChapters",
                "remove_sponsor_segments": ["sponsor", "selfpromo", "interaction"],
            })

        if embed_subs and ffmpeg_available():
            postprocessors.append({"key": "FFmpegEmbedSubtitle"})

        if extra_postprocessors:
            postprocessors.extend(extra_postprocessors)

        # Detect download engine once
        use_aria2c = aria2c_available()

        opts: dict = {
            "format": fmt,
            "outtmpl": outtmpl,

            # ── Container ────────────────────────────────────────────────────
            # MKV holds any codec (AV1, VP9, Opus) without re-encoding.
            # mp4 forces lossy AAC transcode for Opus audio.
            "merge_output_format": "mkv",

            # ── Format Sort — from yt-dlp docs (fields in priority order) ───
            # res: highest resolution first
            # fps: 60fps > 30fps at same resolution
            # hdr: prefer HDR streams (DV > HDR10+ > HDR10 > HLG > SDR)
            # source: prefer original/source-quality encodings from extractor
            # tbr: total bitrate — video+audio combined (largest wins)
            # size: exact filesize if known, else approx (largest = best quality)
            # acodec: best audio codec (opus > aac > mp4a etc)
            # abr: audio bitrate last
            # NOTE: vcodec is intentionally excluded — including it causes yt-dlp
            # to pick a smaller AV1 file over a much larger (better) VP9 file.
            "format_sort": ["res", "fps", "hdr:12", "source", "tbr", "size", "acodec", "abr"],
            # Force this sort order over extractor defaults
            "format_sort_force": True,

            # ── Progress & Output ─────────────────────────────────────────────
            "progress_hooks": [self.rich_progress.hook],
            "quiet": True,
            "no_warnings": True,

            # ── Download Speed & Robustness ──────────────────────────────────
            # aria2c and concurrent_fragment_downloads are MUTUALLY EXCLUSIVE:
            # • aria2c handles its own parallelism (-j/-x/-s args)
            # • concurrent_fragment_downloads is yt-dlp's native parallelism
            # Using both causes fragment-merging failures and corrupted files.
            "concurrent_fragment_downloads": 1 if use_aria2c else 16,
            # NOTE: http_chunk_size is intentionally OMITTED — values ≥ 10 MiB
            # trigger YouTube's per-request throttle wall. yt-dlp's internal
            # anti-throttle logic adapts chunk sizes automatically.
            "retries": MAX_RETRIES,
            "fragment_retries": MAX_RETRIES,
            "extractor_retries": MAX_RETRIES,

            # Write directly to final filename (no orphaned .part files)
            "nopart": True,
            # 1 MB I/O read buffer for efficiency
            "buffersize": 1024 * 1024,

            "postprocessors": postprocessors,

            # ── YouTube Player Client Selection ───────────────────────────────
            # ios, android, web: reliable clients with widest format coverage.
            # tv_embedded: last-resort fallback — can return DRM-protected
            # streams on some videos, causing silent download failures.
            # NOTE: android_vr is intentionally excluded — it returns video-only
            # streams with no matching audio tracks (confirmed cause of silence).
            "extractor_args": {"youtube": {
                "player_client": ["ios", "android", "web", "tv_embedded"],
                # duplicate: expose all streams including alternate CDN copies
                "formats": ["duplicate"],
            }},

            # ── aria2c External Downloader (16 parallel connections) ──────────
            # When aria2c is installed, each file downloads via 16 connections
            # instead of 1 — massively faster on high-bandwidth connections.
            # NOTE: When aria2c is active, concurrent_fragment_downloads is set
            # to 1 (above) to avoid conflicts.
            # Install: winget install aria2
            "external_downloader": "aria2c" if use_aria2c else None,
            "external_downloader_args": {"aria2c": [
                "-c",           # resume partial downloads
                "-j", "16",     # 16 parallel download jobs
                "-x", "16",     # 16 connections per server
                "-s", "16",     # split each file into 16 segments
                "-k", "1M",     # 1 MB chunk size per segment
            ]} if use_aria2c else None,

            "writethumbnail": embed_thumb,
            "writesubtitles": write_subs,
            "embedsubtitles": embed_subs,
            "subtitleslangs": [sub_langs] if (write_subs or embed_subs) else [],
            "keepvideo": False,
            "overwrites": True,           # always re-download, never skip existing file
        }

        if not ffmpeg_available():
            # Without FFmpeg, yt-dlp can't merge separate video+audio streams.
            # Fall back to the best pre-merged stream available.
            opts["format"] = "best"
            opts.pop("merge_output_format", None)
            opts["postprocessors"] = []

        return opts

    # ── Format Preview ────────────────────────
    def _show_format_preview(self, info: dict, fmt_str: str) -> None:
        """Show a summary of the resolved best video + audio format before download."""
        formats = info.get("formats", [])
        if not formats:
            return

        try:
            # Find best video and audio from available formats
            best_video = None
            best_audio = None
            for f in reversed(formats):  # reversed = highest quality last in yt-dlp
                if f.get("vcodec", "none") != "none" and not best_video:
                    best_video = f
                if f.get("acodec", "none") != "none" and f.get("vcodec", "none") == "none" and not best_audio:
                    best_audio = f
                if best_video and best_audio:
                    break

            lines: list[str] = ["[bold white]📋 Selected Format[/bold white]"]
            if best_video:
                res = best_video.get("resolution") or f"{best_video.get('width', '?')}x{best_video.get('height', '?')}"
                vcodec = best_video.get("vcodec", "?").split(".")[0]  # strip codec profile
                fps = best_video.get("fps") or "?"
                tbr = best_video.get("tbr")
                tbr_str = f"{tbr:.0f} kbps" if tbr else "N/A"
                hdr = best_video.get("dynamic_range") or "SDR"
                size = format_bytes(best_video.get("filesize") or best_video.get("filesize_approx"))
                lines.append(
                    f"  🎬 [bold]{res}[/bold] [cyan]{vcodec}[/cyan] "
                    f"[dim]{fps}fps · {tbr_str} · {hdr} · ~{size}[/dim]"
                )
            if best_audio:
                acodec = best_audio.get("acodec", "?").split(".")[0]
                abr = best_audio.get("abr")
                abr_str = f"{abr:.0f} kbps" if abr else "N/A"
                asr = best_audio.get("asr") or "?"
                lines.append(
                    f"  🔊 [cyan]{acodec}[/cyan] [dim]{abr_str} · {asr}Hz[/dim]"
                )

            console.print(Panel("\n".join(lines), border_style="dim"))
        except Exception:
            pass  # Non-critical

    def download(self, url: str, ydl_opts: dict) -> str | None:
        """
        Run yt-dlp download with retry logic.
        Returns the final file path or None on failure.
        """
        delay = RETRY_DELAY
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self.rich_progress.start()
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                self.rich_progress.stop()

                if info is None:
                    return None

                # Resolve final filename
                if info.get("_type") == "playlist":
                    # For playlists, return the directory
                    return DOWNLOAD_DIR

                filename = ydl.prepare_filename(info)
                # If merged to mp4, the extension may have changed
                base, _ = os.path.splitext(filename)
                for ext in (".mp4", ".mkv", ".webm", ".mp3", ".flac", ".aac", ".wav", ".opus", ".m4a"):
                    if os.path.exists(base + ext):
                        return base + ext
                if os.path.exists(filename):
                    return filename
                return filename  # best guess

            except yt_dlp.utils.DownloadError as e:
                self.rich_progress.stop()
                if attempt < MAX_RETRIES:
                    console.print(f"[yellow]⚠  Attempt {attempt}/{MAX_RETRIES} failed. "
                                  f"Retrying in {delay}s…[/yellow]")
                    time.sleep(delay)
                    delay *= 2
                else:
                    console.print(f"[red]❌ Download failed after {MAX_RETRIES} attempts:[/red] {e}")
                    return None
            except Exception as e:
                self.rich_progress.stop()
                console.print(f"[red]❌ Unexpected error:[/red] {e}")
                return None

        return None

    # ── Clip post-processing ──────────────────
    def maybe_clip(self, filepath: str) -> str:
        """Ask user if they want to clip the downloaded file; apply if so."""
        console.print()
        if not Confirm.ask("[cyan]✂  Clip a section of the downloaded video?[/cyan]", default=False):
            return filepath

        start = Prompt.ask("  Start timestamp [dim](e.g. 0:30 or 1:05:20)[/dim]")
        end   = Prompt.ask("  End timestamp   [dim](e.g. 2:00 or 1:07:00)[/dim]")

        base, ext = os.path.splitext(filepath)
        out = base + "_clip" + ext
        clip_video(filepath, start, end, out)

        if os.path.exists(out):
            console.print(f"[green]✅ Clip saved:[/green] {os.path.abspath(out)}")
            if Confirm.ask("  Delete original file?", default=False):
                os.remove(filepath)
                return out
        return filepath

    # ── Subtitle burn-in post-processing ──────
    def maybe_burn_subs(self, filepath: str) -> str:
        """If an .srt was downloaded alongside the video, offer to burn it in."""
        base, ext = os.path.splitext(filepath)
        srt = base + ".en.srt"
        if not os.path.exists(srt):
            # Try any .srt nearby
            folder = os.path.dirname(filepath) or "."
            srts = [f for f in os.listdir(folder) if f.endswith(".srt")]
            if srts:
                srt = os.path.join(folder, srts[0])
            else:
                return filepath

        if Confirm.ask(f"[cyan]🔤 Burn subtitles into video?[/cyan] ([dim]{os.path.basename(srt)}[/dim])",
                       default=False):
            out = base + "_subbed" + ext
            burn_subtitles(filepath, srt, out)
            if os.path.exists(out):
                console.print(f"[green]✅ Subtitled video:[/green] {os.path.abspath(out)}")
                return out
        return filepath

    # ── Main Menu ─────────────────────────────
    def run(self) -> None:
        print_header()

        url = Prompt.ask("[bold]🔗 Enter URL[/bold] [dim](YouTube, Vimeo, Twitter, etc.)[/dim]").strip()
        if not url:
            console.print("[red]No URL provided.[/red]")
            return

        info = self.get_info(url)
        if not info:
            return

        self.display_info(info)
        console.print()

        # ── Mode selection ────────────────────
        console.print(Rule("[bold blue]Download Mode[/bold blue]"))
        modes = Table.grid(padding=(0, 2))
        modes.add_column(style="bold yellow")
        modes.add_column()
        modes.add_row("1", "⚡  [bold]Best Quality Video[/bold]  [dim](4K/8K + best audio, merged via FFmpeg)[/dim]")
        modes.add_row("2", "🎵  [bold]Audio Only[/bold]          [dim](MP3 / FLAC / AAC / WAV / OPUS)[/dim]")
        modes.add_row("3", "🛠   [bold]Custom Format[/bold]       [dim](choose exactly which stream)[/dim]")
        modes.add_row("4", "📋  [bold]Playlist[/bold]             [dim](download all or a range)[/dim]")
        console.print(modes)
        console.print()

        choice = Prompt.ask("Choice", choices=["1", "2", "3", "4"], default="1")

        # ── Shared options ────────────────────
        console.print()
        console.print(Rule("[dim]Options[/dim]"))
        embed_thumb   = ffmpeg_available() and Confirm.ask("  🖼  Embed thumbnail?",          default=True)
        want_subs     = Confirm.ask("  📝  Download subtitles?",                         default=False)
        embed_subs    = False
        write_subs    = False
        sub_langs     = "en"
        if want_subs:
            sub_langs = Prompt.ask("     Subtitle language(s) [dim](e.g. en, fr, de)[/dim]", default="en")
            embed_subs = ffmpeg_available() and Confirm.ask("     Embed subs into file? (soft-sub)", default=True)
            write_subs = not embed_subs  # write to .srt if not embedding
        sponsorblock  = Confirm.ask("  🚫  Skip sponsored segments (SponsorBlock)?",      default=False)
        console.print()

        # ── Mode: Best Quality ────────────────
        if choice == "1":
            # * = allow cross-container selection (e.g. webm video + m4a audio)
            fmt = "bestvideo*+bestaudio*/best"
            ydl_opts = self._build_ydl_opts(
                fmt,
                embed_thumb=embed_thumb,
                embed_subs=embed_subs,
                write_subs=write_subs,
                sub_langs=sub_langs,
                sponsorblock=sponsorblock,
            )

            # Show resolved format preview before downloading
            self._show_format_preview(info, ydl_opts.get("format", fmt))

            console.print("[cyan]⬇  Downloading best quality…[/cyan]")
            filepath = self.download(url, ydl_opts)

        # ── Mode: Audio Only ──────────────────
        elif choice == "2":
            audio_opts = Table.grid(padding=(0, 2))
            for k, v in enumerate(AUDIO_FORMATS, 1):
                audio_opts.add_row(str(k), f"[cyan]{v.upper()}[/cyan]")
            console.print(audio_opts)
            keys = list(AUDIO_FORMATS.keys())
            idx = IntPrompt.ask("Audio format", default=1, show_default=True)
            idx = max(1, min(idx, len(keys)))
            audio_fmt = keys[idx - 1]

            ydl_opts = self._build_ydl_opts(
                "bestaudio/best",
                audio_only=True,
                audio_fmt=audio_fmt,
                embed_thumb=embed_thumb,
                sponsorblock=sponsorblock,
            )
            console.print(f"[cyan]🎵 Extracting audio as {audio_fmt.upper()}…[/cyan]")
            filepath = self.download(url, ydl_opts)

        # ── Mode: Custom Format ───────────────
        elif choice == "3":
            valid_ids = self.list_formats(info)
            f_id = Prompt.ask("\n[bold]Enter Format ID[/bold]").strip()

            if f_id not in valid_ids:
                console.print("[red]Invalid format ID.[/red]")
                return

            # Smart audio-merge for video-only streams
            selected = next((f for f in info.get("formats", []) if f.get("format_id") == f_id), None)
            final_fmt = f_id
            if selected:
                if selected.get("vcodec", "none") != "none" and selected.get("acodec", "none") == "none":
                    console.print("[dim]ℹ  Video-only stream → merging with best audio via FFmpeg[/dim]")
                    final_fmt = f"{f_id}+bestaudio/best"

            ydl_opts = self._build_ydl_opts(
                final_fmt,
                embed_thumb=embed_thumb,
                embed_subs=embed_subs,
                write_subs=write_subs,
                sub_langs=sub_langs,
                sponsorblock=sponsorblock,
            )
            console.print("[cyan]⬇  Downloading selected format…[/cyan]")
            filepath = self.download(url, ydl_opts)

        # ── Mode: Playlist ────────────────────
        elif choice == "4":
            start_idx = IntPrompt.ask("  Start index [dim](1 = first)[/dim]", default=1)
            end_idx   = Prompt.ask("  End index   [dim](leave blank for all)[/dim]", default="").strip()

            fmt = "bestvideo*+bestaudio*/best"
            ydl_opts = self._build_ydl_opts(
                fmt,
                embed_thumb=embed_thumb,
                embed_subs=embed_subs,
                write_subs=write_subs,
                sub_langs=sub_langs,
                sponsorblock=sponsorblock,
            )
            ydl_opts["outtmpl"] = f"{DOWNLOAD_DIR}/%(playlist_index)s - %(title)s.%(ext)s"
            ydl_opts["playliststart"] = start_idx
            if end_idx:
                ydl_opts["playlistend"] = int(end_idx)

            # Anti-throttle: random delay between playlist items
            ydl_opts["sleep_interval"] = 2
            ydl_opts["max_sleep_interval"] = 5

            console.print(f"[cyan]📋 Downloading playlist…[/cyan]")
            filepath = self.download(url, ydl_opts)

        else:
            return

        # ── Post-download actions ─────────────
        console.print()
        if filepath and os.path.isfile(filepath):
            console.print(Panel(
                f"[bold green]✅ Download complete![/bold green]\n"
                f"[dim]Saved:[/dim] [cyan]{os.path.abspath(filepath)}[/cyan]",
                border_style="green",
            ))

            # Verify download quality with ffprobe
            expected_dur = info.get("duration", 0) if not info.get("_type") == "playlist" else 0
            verify_download(filepath, expected_duration=expected_dur)

            # Add metadata
            if ffmpeg_available() and choice in ("1", "3"):
                title   = info.get("title", "")
                artist  = info.get("uploader") or info.get("channel") or ""
                year    = str(info.get("upload_date", ""))[:4]
                comment = info.get("webpage_url", "")
                add_metadata(filepath, title=title, artist=artist, year=year, comment=comment)

            # Optional clip
            if choice in ("1", "3") and ffmpeg_available():
                filepath = self.maybe_clip(filepath)

            # Optional subtitle burn-in
            if write_subs and ffmpeg_available() and choice in ("1", "3"):
                filepath = self.maybe_burn_subs(filepath)

            console.print(f"\n[dim]Final path:[/dim] [bold cyan]{os.path.abspath(filepath)}[/bold cyan]")

        elif filepath == DOWNLOAD_DIR:
            console.print(Panel(
                f"[bold green]✅ Playlist downloaded![/bold green]\n"
                f"[dim]Folder:[/dim] [cyan]{os.path.abspath(DOWNLOAD_DIR)}[/cyan]",
                border_style="green",
            ))
        else:
            console.print("[red]⚠  Could not determine output file.[/red]")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    try:
        SuperDownloader().run()
    except KeyboardInterrupt:
        console.print("\n\n[bold red]⏹  Cancelled by user.[/bold red]")
    except Exception as e:
        console.print(f"\n[red]Fatal error:[/red] {e}")
        raise