"""Persistent local jobs: Streamlit may rerun while FFmpeg continues safely."""
from __future__ import annotations

import json
import argparse
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from clipper_main import parse_timestamps, time_to_seconds, validate_chapters
from video_processing import VideoProcessingError, copy_chapter_fast, ensure_free_space, probe_video, render_chapter


def _now():
    return datetime.now(timezone.utc).isoformat()


class ChapterJob:
    def __init__(self, run_dir):
        self.run_dir = Path(run_dir)
        self.status_path = self.run_dir / "job.json"

    def _write(self, data):
        temporary = self.status_path.with_suffix(".partial")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(self.status_path)

    def read(self):
        return json.loads(self.status_path.read_text(encoding="utf-8"))

    def update(self, **changes):
        state = self.read()
        state.update(changes)
        state["updated_at"] = _now()
        self._write(state)

    @classmethod
    def create(cls, run_dir, source_path, timestamp_text, intro_path=None, outro_path=None, fast_copy=False):
        job = cls(run_dir)
        job.run_dir.mkdir(parents=True, exist_ok=False)
        job._write({
            "state": "queued", "created_at": _now(), "updated_at": _now(), "source_path": str(source_path),
            "timestamp_text": timestamp_text, "intro_path": str(intro_path) if intro_path else None,
            "outro_path": str(outro_path) if outro_path else None, "fast_copy": fast_copy, "progress": 0,
            "message": "Queued", "outputs": [], "log_dir": str(job.run_dir),
        })
        return job

    def start(self):
        """Detach rendering from Streamlit so browser/server reruns do not kill it."""
        command = [sys.executable, str(Path(__file__).resolve()), "--run", str(self.run_dir)]
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)

    def _run(self):
        try:
            state = self.read()
            self.update(state="running", message="Inspecting source video...")
            source_info = probe_video(state["source_path"])
            chapters = parse_timestamps(state["timestamp_text"])
            validate_chapters(chapters, source_info["duration"])
            paths = [state["source_path"], state.get("intro_path"), state.get("outro_path")]
            ensure_free_space(self.run_dir, paths)
            outputs = []
            for index, chapter in enumerate(chapters):
                start = time_to_seconds(chapter["time"])
                end = time_to_seconds(chapters[index + 1]["time"]) if index + 1 < len(chapters) else source_info["duration"]
                output_path = self.run_dir / f"{index + 1:02d}_{chapter['title']}.mp4"
                self.update(message=f"Rendering {index + 1}/{len(chapters)}: {chapter['title']}")
                def report(rendered_seconds, index=index, start=start, end=end):
                    fraction = (index + min(1.0, rendered_seconds / max(end - start, 0.01))) / len(chapters)
                    self.update(progress=round(fraction * 100, 1))
                if state["fast_copy"] and not state.get("intro_path") and not state.get("outro_path"):
                    result = copy_chapter_fast(state["source_path"], start, end, output_path, self.run_dir)
                else:
                    result = render_chapter(state["source_path"], start, end, output_path, self.run_dir, state.get("intro_path"), state.get("outro_path"), report)
                outputs.append(result["path"])
                self.update(outputs=outputs)
            self.update(state="completed", progress=100, message="Completed")
        except Exception as exc:
            self.update(state="failed", message=str(exc), traceback=traceback.format_exc())


def cleanup_completed_jobs(root, days=7):
    """Delete only completed/failed job directories older than the retention period."""
    import shutil
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    for child in Path(root).iterdir() if Path(root).exists() else []:
        status = child / "job.json"
        if not child.is_dir() or not status.exists() or child.stat().st_mtime >= cutoff:
            continue
        state = json.loads(status.read_text(encoding="utf-8")).get("state")
        if state in {"completed", "failed"}:
            shutil.rmtree(child)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run one persisted Video Clipper job.")
    parser.add_argument("--run", required=True, help="Path to the job directory")
    arguments = parser.parse_args()
    ChapterJob(arguments.run)._run()
