"""
╔════════════════════════════════════════════════════════╗
║          YT-DLP + FFMPEG SUPER DOWNLOADER              ║
║   Best quality · Audio rip · Subtitles · Thumbnails    ║
╚════════════════════════════════════════════════════════╝

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
CONCURRENT_DL  = 5          # parallel fragment downloads
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


def remux_mkv_to_mp4(mkv_path: str) -> str:
    """
    Losslessly remux an MKV file to MP4 using stream-copy (no re-encoding).
    MKV is used during merge to support any codec (AV1/VP9); then we swap
    the container to MP4 without touching the video/audio data.
    Returns the new .mp4 path, or the original mkv path if remux fails.
    """
    mp4_path = os.path.splitext(mkv_path)[0] + ".mp4"
    with console.status("[cyan]📦 Remuxing MKV → MP4 (lossless)…", spinner="dots"):
        result = run_ffmpeg(
            "-i", mkv_path,
            "-c", "copy",          # stream-copy: no re-encode, no quality loss
            "-movflags", "+faststart",  # moves index to front for web streaming
            mp4_path,
        )
    if result.returncode == 0 and os.path.exists(mp4_path):
        os.remove(mkv_path)        # clean up the intermediate MKV
        console.print(f"[green]✅ Remuxed to MP4:[/green] {os.path.basename(mp4_path)}")
        return mp4_path
    else:
        console.print("[yellow]⚠  Remux failed — keeping MKV.[/yellow]")
        return mkv_path


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
            # 'default' lets yt-dlp auto-negotiate the best client combo
            "extractor_args": {"youtube": {"player_client": ["default"]}},

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
        audio_quality: str = "0",
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
                "preferredquality": audio_quality,
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

        opts: dict = {
            "format": fmt,
            "outtmpl": outtmpl,
            # mkv supports ALL codecs (AV1, VP9, H.265, Opus, etc.) — mp4 can't
            "merge_output_format": "mkv",
            "progress_hooks": [self.rich_progress.hook],
            "quiet": True,
            "no_warnings": True,
            "concurrent_fragment_downloads": CONCURRENT_DL,
            "retries": MAX_RETRIES,
            "fragment_retries": MAX_RETRIES,
            "postprocessors": postprocessors,
            # 'default' lets yt-dlp auto-negotiate the best client combo
            "extractor_args": {"youtube": {"player_client": ["default"]}},

            # Force yt-dlp to pick highest res > highest bitrate > best codec
            "format_sort": ["res:4320", "br", "codec:av01:vp9.2:vp9:h265:h264"],
            "format_sort_force": True,
            "prefer_free_formats": True,
            "writethumbnail": embed_thumb,
            "writesubtitles": write_subs,
            "embedsubtitles": embed_subs,
            "subtitleslangs": [sub_langs] if (write_subs or embed_subs) else [],
            "keepvideo": False,
        }

        if not ffmpeg_available():
            # Graceful degradation
            opts["format"] = "best"
            opts.pop("merge_output_format", None)
            opts["postprocessors"] = []

        return opts

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
                # Merged output is mkv; check likely extensions in priority order
                base, _ = os.path.splitext(filename)
                for ext in (".mkv", ".mp4", ".webm", ".mp3", ".flac", ".aac", ".wav", ".opus", ".m4a"):
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

    def prompt_video_quality(self) -> tuple[str, str]:
        qualities = [
            ("Best Available (Up to 8K)", "bestvideo+bestaudio/best"),
            ("8K (4320p)", "bestvideo[height<=4320]+bestaudio/best[height<=4320]/best"),
            ("4K (2160p)", "bestvideo[height<=2160]+bestaudio/best[height<=2160]/best"),
            ("1440p (2K)", "bestvideo[height<=1440]+bestaudio/best[height<=1440]/best"),
            ("1080p (FHD)", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"),
            ("720p (HD)", "bestvideo[height<=720]+bestaudio/best[height<=720]/best"),
            ("480p (SD)", "bestvideo[height<=480]+bestaudio/best[height<=480]/best"),
            ("360p", "bestvideo[height<=360]+bestaudio/best[height<=360]/best"),
            ("240p", "bestvideo[height<=240]+bestaudio/best[height<=240]/best"),
            ("144p", "bestvideo[height<=144]+bestaudio/best[height<=144]/best"),
            ("Worst Available (Smallest)", "worstvideo+worstaudio/worst"),
        ]
        quality_opts = Table.grid(padding=(0, 2))
        quality_opts.add_column(style="bold yellow")
        quality_opts.add_column()
        for i, (name, _) in enumerate(qualities, 1):
            quality_opts.add_row(str(i), f"[cyan]{name}[/cyan]")
        
        console.print()
        console.print(Rule("[dim]Video Quality[/dim]"))
        console.print(quality_opts)
        q_idx = IntPrompt.ask("Select Quality", default=1, show_default=True)
        q_idx = max(1, min(q_idx, len(qualities)))
        return qualities[q_idx - 1][0], qualities[q_idx - 1][1]

    def prompt_audio_quality(self) -> str:
        qualities = [
            ("Best (VBR/Lossless)", "0"),
            ("320 kbps (CBR)", "320"),
            ("256 kbps (CBR)", "256"),
            ("192 kbps (CBR)", "192"),
            ("128 kbps (CBR)", "128"),
            ("96 kbps (CBR)", "96"),
            ("64 kbps (CBR)", "64"),
            ("Worst", "9"),
        ]
        quality_opts = Table.grid(padding=(0, 2))
        quality_opts.add_column(style="bold yellow")
        quality_opts.add_column()
        for i, (name, _) in enumerate(qualities, 1):
            quality_opts.add_row(str(i), f"[cyan]{name}[/cyan]")
        
        console.print()
        console.print(Rule("[dim]Audio Extraction Quality[/dim]"))
        console.print(quality_opts)
        q_idx = IntPrompt.ask("Select Quality", default=1, show_default=True)
        q_idx = max(1, min(q_idx, len(qualities)))
        return qualities[q_idx - 1][1]

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
        modes.add_row("1", "⚡  [bold]Video Download[/bold]        [dim](Choose resolution from 144p to 8K)[/dim]")
        modes.add_row("2", "🎵  [bold]Audio Only[/bold]          [dim](MP3 / FLAC / AAC / WAV / OPUS)[/dim]")
        modes.add_row("3", "🛠   [bold]Custom Format[/bold]       [dim](choose exactly which stream via ID)[/dim]")
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

        # ── Mode: Video Download ────────────────
        if choice == "1":
            q_name, fmt = self.prompt_video_quality()
            ydl_opts = self._build_ydl_opts(
                fmt,
                embed_thumb=embed_thumb,
                embed_subs=embed_subs,
                write_subs=write_subs,
                sub_langs=sub_langs,
                sponsorblock=sponsorblock,
            )
            console.print(f"[cyan]⬇  Downloading video: {q_name}…[/cyan]")
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

            audio_quality = "0"
            if audio_fmt in ("mp3", "aac", "m4a", "opus"):
                audio_quality = self.prompt_audio_quality()

            ydl_opts = self._build_ydl_opts(
                "bestaudio/best",
                audio_only=True,
                audio_fmt=audio_fmt,
                audio_quality=audio_quality,
                embed_thumb=embed_thumb,
                sponsorblock=sponsorblock,
            )
            q_display = audio_quality if audio_quality != "0" else "Best"
            console.print(f"[cyan]🎵 Extracting audio as {audio_fmt.upper()} (Quality: {q_display})…[/cyan]")
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

            q_name, fmt = self.prompt_video_quality()

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

            console.print(f"[cyan]📋 Downloading playlist: {q_name}…[/cyan]")
            filepath = self.download(url, ydl_opts)

        else:
            return

        # ── Post-download actions ─────────────
        console.print()
        if filepath and os.path.isfile(filepath):

            # Remux MKV → MP4 (lossless container swap, no quality loss)
            if ffmpeg_available() and choice in ("1", "3", "4") and filepath.endswith(".mkv"):
                filepath = remux_mkv_to_mp4(filepath)

            console.print(Panel(
                f"[bold green]✅ Download complete![/bold green]\n"
                f"[dim]Saved:[/dim] [cyan]{os.path.abspath(filepath)}[/cyan]",
                border_style="green",
            ))

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