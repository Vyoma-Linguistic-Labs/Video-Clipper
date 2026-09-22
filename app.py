"""Local, disk-backed Streamlit interface for Video Chapter Clipper."""
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import streamlit as st

from clipper_main import download_youtube_video, parse_timestamps, time_to_seconds
from job_manager import ChapterJob, cleanup_completed_jobs
from video_processing import probe_video

st.set_page_config(page_title="Video Chapter Clipper", page_icon=":movie_camera:", layout="wide")
WORK_DIR = Path("streamlit_workdir")
UPLOAD_DIR, JOB_DIR = WORK_DIR / "uploads", WORK_DIR / "jobs"
SAMPLE_TIMESTAMPS = """00:00 Introduction and setup
01:45 Deep dive into data preprocessing
12:30 Training the model architecture
24:15 Evaluating the validation results
35:02 Q&A and final thoughts"""


def save_upload(uploaded_file, destination_dir):
    """Copy incrementally to disk; do not retain an additional Python byte copy."""
    if uploaded_file is None:
        return None
    destination = Path(destination_dir) / Path(uploaded_file.name).name
    with destination.open("wb") as target:
        while chunk := uploaded_file.read(8 * 1024 * 1024):
            target.write(chunk)
    return destination


def chapter_preview(text):
    return [{"#": index, "Start": item["time"], "Seconds": time_to_seconds(item["time"]), "Output": item["title"]}
            for index, item in enumerate(parse_timestamps(text), start=1)]


def load_job():
    run_dir = st.session_state.get("job_dir")
    if not run_dir:
        return None, None
    job = ChapterJob(run_dir)
    try:
        return job, job.read()
    except (FileNotFoundError, ValueError):
        return None, None


cleanup_completed_jobs(JOB_DIR)
if "job_dir" not in st.session_state:
    st.session_state.job_dir = None

st.title("Video Chapter Clipper")
st.caption("Jobs run locally in a background worker. You can safely refresh the page; outputs and FFmpeg logs remain on disk.")

with st.sidebar:
    st.header("Reliability")
    include_intro = st.toggle("Use intro video", value=False)
    include_outro = st.toggle("Use outro video", value=False)
    preserve_original = st.toggle(
        "Preserve original video and audio",
        value=True,
        disabled=include_intro or include_outro,
        help="For plain splits, copies the original encoded streams without quality loss. Cuts align to nearby keyframes.",
    )
    st.caption("Turn off intro/outro to use bit-for-bit stream copy. Frame-accurate cuts and stitching use visually-lossless re-encoding.")
    st.caption("Completed jobs are cleaned after seven days. Keep several times the source size free on disk.")

source_mode = st.radio("Main source", ["Upload video", "YouTube link"], horizontal=True)
if source_mode == "Upload video":
    main_upload = st.file_uploader("Main video", type=["mp4", "mov", "mkv", "webm", "m4v"])
    youtube_url = ""
else:
    main_upload = None
    youtube_url = st.text_input("YouTube URL", placeholder="https://www.youtube.com/watch?v=...").strip()
left, right = st.columns([1.15, 0.85], gap="large")
with left:
    timestamp_text = st.text_area("Description or timestamps", value=SAMPLE_TIMESTAMPS, height=260)
with right:
    intro_upload = st.file_uploader("Intro video", type=["mp4", "mov", "mkv", "webm", "m4v"], disabled=not include_intro)
    outro_upload = st.file_uploader("Outro video", type=["mp4", "mov", "mkv", "webm", "m4v"], disabled=not include_outro)

chapters = chapter_preview(timestamp_text)
st.metric("Chapters", len(chapters))
if chapters:
    st.dataframe(chapters, hide_index=True, use_container_width=True)
else:
    st.warning("Enter at least one valid MM:SS or HH:MM:SS timestamp.")

if st.button("Create clips", type="primary", disabled=not ((main_upload or youtube_url) and chapters)):
    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
    input_dir, run_dir = UPLOAD_DIR / run_id, JOB_DIR / run_id
    input_dir.mkdir(parents=True, exist_ok=False)
    try:
        main_path = download_youtube_video(youtube_url, input_dir) if youtube_url else save_upload(main_upload, input_dir)
        intro_path = save_upload(intro_upload, input_dir) if include_intro else None
        outro_path = save_upload(outro_upload, input_dir) if include_outro else None
        probe_video(main_path)  # fail before a long-running job starts
        job = ChapterJob.create(run_dir, main_path, timestamp_text, intro_path, outro_path, preserve_original)
        job.start()
        st.session_state.job_dir = str(run_dir)
        st.rerun()
    except Exception as exc:
        st.error(str(exc))

job, status = load_job()
if status:
    st.divider()
    st.subheader("Current job")
    st.progress(int(status.get("progress", 0)))
    st.write(f"**{status['state'].title()}** — {status.get('message', '')}")
    if status["state"] in {"queued", "running"}:
        st.caption("Use Refresh status while the local worker renders. Closing the browser does not stop the job.")
        if st.button("Refresh status"):
            st.rerun()
    if status["state"] == "failed":
        st.error(status["message"])
        st.code(status.get("traceback", ""), language="text")
    if status["state"] == "completed":
        output_paths = [Path(path) for path in status.get("outputs", []) if Path(path).exists()]
        if output_paths:
            zip_path = Path(st.session_state.job_dir) / "video_chapters.zip"
            if not zip_path.exists():
                import zipfile
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
                    for output_path in output_paths:
                        archive.write(output_path, output_path.name)
            with zip_path.open("rb") as archive:
                st.download_button("Download all clips as ZIP", data=archive, file_name=zip_path.name, mime="application/zip")
            for output_path in output_paths:
                with output_path.open("rb") as video:
                    st.download_button(output_path.name, data=video, file_name=output_path.name, mime="video/mp4")
        st.caption(f"Logs and outputs: {status['log_dir']}")
