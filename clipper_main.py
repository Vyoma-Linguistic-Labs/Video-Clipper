import re
import subprocess
import sys
from pathlib import Path


INVALID_FILENAME_CHARS = r'[\\/*?:"<>|]'
TIMESTAMP_PATTERN = r'(\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2})'


def parse_timestamps(text_data):
    """Extract timestamped chapter titles from pasted chapter text."""
    chapters = []
    for line in text_data.strip().split("\n"):
        match = re.search(TIMESTAMP_PATTERN, line)
        if match:
            timestamp = match.group(1)
            title = re.sub(INVALID_FILENAME_CHARS, "", line.replace(timestamp, "").strip(" -:|[]()"))
            title = re.sub(r"\s+", "_", title).strip("_") or "segment"
            chapters.append({"time": timestamp, "title": title})
    return chapters


def time_to_seconds(time_str):
    parts = list(map(int, time_str.split(":")))
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def validate_chapters(chapters, total_duration=None):
    if not chapters:
        raise ValueError("No valid timestamps found.")
    previous_start = -1
    for chapter in chapters:
        start_sec = time_to_seconds(chapter["time"])
        if start_sec <= previous_start:
            raise ValueError("Timestamps must be in ascending order.")
        if total_duration is not None and start_sec >= total_duration:
            raise ValueError(f"Timestamp {chapter['time']} starts after the video ends.")
        previous_start = start_sec


def split_and_stitch_video(video_path, timestamp_text, intro_path=None, outro_path=None,
                           output_dir="output_segments", output_extension=".mp4", progress_callback=None):
    """Compatibility API backed by FFmpeg rather than in-process MoviePy rendering."""
    from video_processing import probe_video, render_chapter
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    chapters = parse_timestamps(timestamp_text)
    source = probe_video(video_path)
    validate_chapters(chapters, source["duration"])
    output_paths = []
    for index, chapter in enumerate(chapters):
        start = time_to_seconds(chapter["time"])
        end = time_to_seconds(chapters[index + 1]["time"]) if index + 1 < len(chapters) else source["duration"]
        if progress_callback:
            progress_callback(index, len(chapters), f"Rendering {index + 1}/{len(chapters)}: {chapter['title']}")
        output_path = output_dir / f"{index + 1:02d}_{chapter['title']}{output_extension}"
        render_chapter(video_path, start, end, output_path, output_dir, intro_path, outro_path)
        output_paths.append(output_path)
    if progress_callback:
        progress_callback(len(chapters), len(chapters), "Done")
    return output_paths


def download_youtube_video(url, output_dir, progress_callback=None):
    """Download a YouTube video with yt-dlp and return the local file path."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "yt_dlp", "-f", "b[ext=mp4]/best", "--js-runtimes", "node",
               "--no-simulate", "-o", str(output_dir / "%(title).180B.%(ext)s"), "--print", "after_move:filepath", url]
    if progress_callback:
        progress_callback("Downloading YouTube video...")
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"YouTube download failed: {exc.stderr.strip() or exc.stdout.strip()}") from exc
    paths = [Path(line.strip()) for line in result.stdout.splitlines() if line.strip() and Path(line.strip()).exists()]
    if not paths:
        paths = sorted(output_dir.glob("*"), key=lambda path: path.stat().st_mtime)
    if not paths:
        raise RuntimeError("YouTube download finished without returning a file path.")
    return paths[-1]
