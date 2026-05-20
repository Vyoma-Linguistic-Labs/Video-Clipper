import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from moviepy.editor import VideoFileClip, concatenate_videoclips
except ModuleNotFoundError:
    from moviepy import VideoFileClip, concatenate_videoclips


INVALID_FILENAME_CHARS = r'[\\/*?:"<>|]'
TIMESTAMP_PATTERN = r'(\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2})'


def parse_timestamps(text_data):
    """Extract timestamps and segment titles from pasted chapter text."""
    chapters = []

    for line in text_data.strip().split("\n"):
        match = re.search(TIMESTAMP_PATTERN, line)
        if match:
            timestamp = match.group(1)
            title = line.replace(timestamp, "").strip(" -:|[]()")
            title = re.sub(INVALID_FILENAME_CHARS, "", title)
            title = re.sub(r"\s+", "_", title).strip("_")
            if not title:
                title = "segment"
            chapters.append({"time": timestamp, "title": title})

    return chapters


def time_to_seconds(time_str):
    """Convert HH:MM:SS or MM:SS to total seconds."""
    parts = list(map(int, time_str.split(":")))
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def _subclip(video_clip, start_sec, end_sec):
    """Support MoviePy v1 and v2 clip APIs."""
    if hasattr(video_clip, "subclip"):
        return video_clip.subclip(start_sec, end_sec)
    return video_clip.subclipped(start_sec, end_sec)


def validate_chapters(chapters, total_duration=None):
    if not chapters:
        raise ValueError("No valid timestamps found.")

    previous_start = -1
    for chapter in chapters:
        start_sec = time_to_seconds(chapter["time"])
        if start_sec <= previous_start:
            raise ValueError("Timestamps must be in ascending order.")
        if total_duration is not None and start_sec >= total_duration:
            raise ValueError(
                f"Timestamp {chapter['time']} starts after the video ends."
            )
        previous_start = start_sec


def split_and_stitch_video(
    video_path,
    timestamp_text,
    intro_path=None,
    outro_path=None,
    output_dir="output_segments",
    output_extension=".mp4",
    progress_callback=None,
):
    """Split a video into timestamped chapters and optionally add intro/outro clips."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    chapters = parse_timestamps(timestamp_text)
    metadata_video = None
    output_paths = []

    try:
        metadata_video = VideoFileClip(str(video_path))
        total_duration = metadata_video.duration
        validate_chapters(chapters, total_duration=total_duration)
        metadata_video.close()
        metadata_video = None

        total_segments = len(chapters)

        for index, chapter in enumerate(chapters):
            start_sec = time_to_seconds(chapter["time"])
            end_sec = (
                time_to_seconds(chapters[index + 1]["time"])
                if index < total_segments - 1
                else total_duration
            )

            if end_sec <= start_sec:
                raise ValueError(f"Invalid end time for segment {index + 1}.")

            if progress_callback:
                progress_callback(
                    index,
                    total_segments,
                    f"Rendering {index + 1}/{total_segments}: {chapter['title']}",
                )

            core_subclip = None
            final_clip = None
            segment_main_video = None
            intro_clip = None
            outro_clip = None

            try:
                segment_main_video = VideoFileClip(str(video_path))
                core_subclip = _subclip(segment_main_video, start_sec, end_sec)
                clips_to_stitch = []
                if intro_path and os.path.exists(intro_path):
                    intro_clip = VideoFileClip(str(intro_path))
                    clips_to_stitch.append(intro_clip)
                clips_to_stitch.append(core_subclip)
                if outro_path and os.path.exists(outro_path):
                    outro_clip = VideoFileClip(str(outro_path))
                    clips_to_stitch.append(outro_clip)

                final_clip = concatenate_videoclips(clips_to_stitch, method="compose")
                output_filename = f"{index + 1:02d}_{chapter['title']}{output_extension}"
                output_path = output_dir / output_filename
                temp_audiofile = output_dir / f"temp-audio-{index + 1}.m4a"

                final_clip.write_videofile(
                    str(output_path),
                    codec="libx264",
                    audio_codec="aac",
                    temp_audiofile=str(temp_audiofile),
                    remove_temp=True,
                    logger=None,
                )
            finally:
                if final_clip:
                    final_clip.close()
                if core_subclip:
                    core_subclip.close()
                if intro_clip:
                    intro_clip.close()
                if outro_clip:
                    outro_clip.close()
                if segment_main_video:
                    segment_main_video.close()

            output_paths.append(output_path)

        if progress_callback:
            progress_callback(total_segments, total_segments, "Done")

        return output_paths
    finally:
        if metadata_video:
            metadata_video.close()


def download_youtube_video(url, output_dir, progress_callback=None):
    """Download a YouTube video with yt-dlp and return the local file path."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = output_dir / "%(title).180B.%(ext)s"

    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "-f",
        "b[ext=mp4]/best",
        "--js-runtimes",
        "node",
        "--no-simulate",
        "-o",
        str(output_template),
        "--print",
        "after_move:filepath",
        url,
    ]

    if progress_callback:
        progress_callback("Downloading YouTube video...")

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "yt-dlp is not installed. Install it with `pip install yt-dlp`."
        ) from exc
    except subprocess.CalledProcessError as exc:
        error_text = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(f"YouTube download failed: {error_text}") from exc

    downloaded_paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    existing_paths = [Path(path) for path in downloaded_paths if Path(path).exists()]

    if not existing_paths:
        existing_paths = sorted(output_dir.glob("*"), key=lambda path: path.stat().st_mtime)

    if not existing_paths:
        raise RuntimeError("YouTube download finished without returning a file path.")

    return existing_paths[-1]


if __name__ == "__main__":
    INPUT_VIDEO = "main_lecture.mp4"
    INTRO_PATH = "my_intro_clip.mp4"
    OUTRO_PATH = "my_outro_clip.mp4"

    MY_TIMESTAMPS = """
    00:00 Introduction and setup
    01:45 Deep dive into data preprocessing
    12:30 Training the model architecture
    24:15 Evaluating the validation results
    35:02 Q&A and final thoughts
    """

    created_files = split_and_stitch_video(
        video_path=INPUT_VIDEO,
        timestamp_text=MY_TIMESTAMPS,
        intro_path=INTRO_PATH,
        outro_path=OUTRO_PATH,
    )

    print("Created files:")
    for created_file in created_files:
        print(created_file)
