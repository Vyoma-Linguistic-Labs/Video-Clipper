"""Reliable, disk-backed FFmpeg video processing primitives.

MoviePy is deliberately not used here: FFmpeg owns decoding/encoding and exposes
progress, error output, and exit status for each independently recoverable job.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path


class VideoProcessingError(RuntimeError):
    pass


def ffmpeg_executable():
    executable = shutil.which("ffmpeg")
    if not executable:
        raise VideoProcessingError("FFmpeg was not found on PATH. Install FFmpeg and restart the app.")
    return executable


def ffprobe_executable():
    executable = shutil.which("ffprobe")
    if not executable:
        raise VideoProcessingError("ffprobe was not found on PATH. Install the full FFmpeg package.")
    return executable


def probe_video(path):
    """Return duration, dimensions, and audio availability using ffprobe."""
    result = subprocess.run(
        [ffprobe_executable(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise VideoProcessingError(result.stderr.strip() or "ffprobe could not read the video.")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video:
        raise VideoProcessingError("The selected file has no video stream.")
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VideoProcessingError("The video duration could not be determined.") from exc
    return {
        "duration": duration,
        "width": int(video["width"]),
        "height": int(video["height"]),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
        "video_codec": video.get("codec_name"),
        "audio_codec": next((stream.get("codec_name") for stream in streams if stream.get("codec_type") == "audio"), None),
    }


def required_free_space(source_paths, expected_output_bytes=0):
    """Conservative estimate: input plus two output-sized working copies."""
    input_bytes = sum(Path(path).stat().st_size for path in source_paths if path and Path(path).exists())
    return input_bytes + max(expected_output_bytes, input_bytes) * 2


def ensure_free_space(work_dir, source_paths, expected_output_bytes=0):
    needed = required_free_space(source_paths, expected_output_bytes)
    available = shutil.disk_usage(work_dir).free
    if available < needed:
        raise VideoProcessingError(
            f"Insufficient disk space: need about {needed / 1024**3:.1f} GB, "
            f"but only {available / 1024**3:.1f} GB is free."
        )


def _escape_filter_path(value):
    return str(value).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _video_filter(input_index, label, width, height, start=None, end=None):
    trim = "" if start is None else f"trim=start={start:.6f}:end={end:.6f},"
    return (
        f"[{input_index}:v]{trim}setpts=PTS-STARTPTS,"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1[{label}v]"
    )


def _audio_filter(input_index, label, duration, has_audio, start=None, end=None):
    if has_audio:
        trim = "" if start is None else f"atrim=start={start:.6f}:end={end:.6f},"
        return f"[{input_index}:a]{trim}asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[{label}a]"
    return f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[{label}a]"


def _run_ffmpeg(command, progress_callback, log_path):
    """Run FFmpeg, persist stderr, and report monotonic out_time progress."""
    started = time.monotonic()
    with Path(log_path).open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=log_file,
            text=True,
            bufsize=1,
        )
        last_seconds = 0.0
        for line in process.stdout or []:
            key, _, value = line.strip().partition("=")
            if key == "out_time_ms":
                last_seconds = int(value or 0) / 1_000_000
                if progress_callback:
                    progress_callback(last_seconds)
        returncode = process.wait()
    if returncode:
        tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[-4000:]
        raise VideoProcessingError(f"FFmpeg failed (exit {returncode}). See {log_path}.\n{tail}")
    return time.monotonic() - started


def render_chapter(source_path, start, end, output_path, work_dir, intro_path=None, outro_path=None, progress_callback=None):
    """Render one frame-accurate chapter with optional normalized intro/outro."""
    source_path, output_path, work_dir = map(Path, (source_path, output_path, work_dir))
    source_info = probe_video(source_path)
    duration = end - start
    if duration <= 0:
        raise VideoProcessingError("Chapter end must be after its start.")
    assets = [("source", source_path, start, end, source_info)]
    if intro_path:
        info = probe_video(intro_path)
        assets.insert(0, ("intro", Path(intro_path), None, None, info))
    if outro_path:
        info = probe_video(outro_path)
        assets.append(("outro", Path(outro_path), None, None, info))
    ensure_free_space(work_dir, [item[1] for item in assets], output_path.stat().st_size if output_path.exists() else 0)
    width, height = source_info["width"], source_info["height"]
    command = [ffmpeg_executable(), "-y", "-hide_banner", "-nostats"]
    for _, path, _, _, _ in assets:
        command.extend(["-i", str(path)])
    filters, labels, total_duration = [], [], 0.0
    for index, (name, _, item_start, item_end, info) in enumerate(assets):
        item_duration = (item_end - item_start) if item_start is not None else info["duration"]
        filters.append(_video_filter(index, name, width, height, item_start, item_end))
        filters.append(_audio_filter(index, name, item_duration, info["has_audio"], item_start, item_end))
        labels.extend([f"[{name}v]", f"[{name}a]"])
        total_duration += item_duration
    filters.append("".join(labels) + f"concat=n={len(assets)}:v=1:a=1[v][a]")
    temporary = output_path.with_suffix(".partial.mp4")
    command.extend([
        "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]", "-map_metadata", "0",
        # Frame-accurate composition cannot remain byte-identical. These settings
        # aim for visually lossless video and transparent high-bitrate AAC audio.
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-fps_mode", "passthrough",
        "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", "-progress", "pipe:1", str(temporary),
    ])
    _run_ffmpeg(command, progress_callback, work_dir / f"{output_path.stem}.ffmpeg.log")
    temporary.replace(output_path)
    return {"path": str(output_path), "duration": total_duration}


def copy_chapter_fast(source_path, start, end, output_path, work_dir):
    """Bit-for-bit stream copy; cuts must align to keyframes rather than exact frames."""
    output_path, work_dir = Path(output_path), Path(work_dir)
    temporary = output_path.with_suffix(".partial.mp4")
    command = [
        ffmpeg_executable(), "-y", "-hide_banner", "-ss", f"{start:.6f}", "-to", f"{end:.6f}",
        "-i", str(source_path), "-map", "0", "-map_metadata", "0", "-c", "copy",
        "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(temporary),
    ]
    _run_ffmpeg(command, None, work_dir / f"{output_path.stem}.ffmpeg.log")
    temporary.replace(output_path)
    return {"path": str(output_path), "duration": end - start}
