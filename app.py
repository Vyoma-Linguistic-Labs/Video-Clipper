import io
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import streamlit as st

from clipper_main import (
    download_youtube_video,
    parse_timestamps,
    split_and_stitch_video,
    time_to_seconds,
)


st.set_page_config(
    page_title="Video Chapter Clipper",
    page_icon=":movie_camera:",
    layout="wide",
)


SAMPLE_TIMESTAMPS = """00:00 Introduction and setup
01:45 Deep dive into data preprocessing
12:30 Training the model architecture
24:15 Evaluating the validation results
35:02 Q&A and final thoughts"""

WORK_DIR = Path("streamlit_workdir")
DOWNLOAD_DIR = WORK_DIR / "downloads"
UPLOAD_DIR = WORK_DIR / "uploads"
OUTPUT_DIR = WORK_DIR / "outputs"


def save_uploaded_file(uploaded_file, destination_dir):
    if uploaded_file is None:
        return None

    destination = Path(destination_dir) / uploaded_file.name
    with destination.open("wb") as file:
        file.write(uploaded_file.getbuffer())
    return destination


def build_zip(files):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_name, file_bytes in files:
            zip_file.writestr(file_name, file_bytes)
    archive.seek(0)
    return archive.getvalue()


def render_chapter_preview(timestamp_text):
    chapters = parse_timestamps(timestamp_text)
    if not chapters:
        return []

    preview_rows = []
    for index, chapter in enumerate(chapters, start=1):
        preview_rows.append(
            {
                "#": index,
                "Start": chapter["time"],
                "Seconds": time_to_seconds(chapter["time"]),
                "Output title": chapter["title"],
            }
        )
    return preview_rows


if "clip_results" not in st.session_state:
    st.session_state.clip_results = []
if "zip_results" not in st.session_state:
    st.session_state.zip_results = None
if "downloaded_source_path" not in st.session_state:
    st.session_state.downloaded_source_path = None
if "last_output_dir" not in st.session_state:
    st.session_state.last_output_dir = None


st.markdown(
    """
    <style>
        .block-container {
            padding-top: 2rem;
            padding-bottom: 3rem;
            max-width: 1180px;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.45rem;
        }
        section[data-testid="stSidebar"] {
            min-width: 320px;
        }
        .stButton > button {
            width: 100%;
            min-height: 3rem;
            font-weight: 700;
        }
        .stDownloadButton > button {
            width: 100%;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


st.title("Video Chapter Clipper")

with st.sidebar:
    st.header("Output")
    include_intro = st.toggle("Use intro video", value=True)
    include_outro = st.toggle("Use outro video", value=True)
    st.divider()
    st.caption("Local processing uses your machine's CPU and disk.")


source_mode = st.radio(
    "Main source",
    ["Upload video", "YouTube link"],
    horizontal=True,
)

if source_mode == "Upload video":
    main_upload = st.file_uploader(
        "Main video",
        type=["mp4", "mov", "mkv", "webm", "m4v"],
        accept_multiple_files=False,
    )
    youtube_url = ""
else:
    main_upload = None
    youtube_url = st.text_input("YouTube URL", placeholder="https://www.youtube.com/watch?v=...")

left_column, right_column = st.columns([1.15, 0.85], gap="large")

with left_column:
    timestamp_text = st.text_area(
        "Description or timestamps",
        value=SAMPLE_TIMESTAMPS,
        height=260,
    )

with right_column:
    intro_upload = st.file_uploader(
        "Intro video",
        type=["mp4", "mov", "mkv", "webm", "m4v"],
        disabled=not include_intro,
        accept_multiple_files=False,
    )
    outro_upload = st.file_uploader(
        "Outro video",
        type=["mp4", "mov", "mkv", "webm", "m4v"],
        disabled=not include_outro,
        accept_multiple_files=False,
    )


chapters = render_chapter_preview(timestamp_text)
metric_columns = st.columns(3)
metric_columns[0].metric("Chapters", len(chapters))
metric_columns[1].metric("Intro", "On" if include_intro and intro_upload else "Off")
metric_columns[2].metric("Outro", "On" if include_outro and outro_upload else "Off")

if chapters:
    with st.expander("Chapter preview", expanded=True):
        st.dataframe(chapters, hide_index=True, use_container_width=True)
else:
    st.warning("No timestamps detected yet.")


selected_youtube_url = youtube_url.strip()
has_main_source = main_upload is not None or bool(selected_youtube_url)
process_disabled = not has_main_source or not chapters

process_button = st.button(
    "Create clips",
    type="primary",
    disabled=process_disabled,
)

if process_button:
    st.session_state.clip_results = []
    st.session_state.zip_results = None
    st.session_state.downloaded_source_path = None
    st.session_state.last_output_dir = None

    progress_bar = st.progress(0)
    status_box = st.empty()

    def update_render_progress(current, total, message):
        percent = min(100, int((current / max(total, 1)) * 100))
        progress_bar.progress(percent)
        status_box.info(message)

    def update_download_status(message):
        progress_bar.progress(5)
        status_box.info(message)

    try:
        run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
        input_dir = UPLOAD_DIR / run_id
        download_dir = DOWNLOAD_DIR / run_id
        output_dir = OUTPUT_DIR / run_id
        input_dir.mkdir(parents=True, exist_ok=True)
        download_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        if selected_youtube_url:
            main_video_path = download_youtube_video(
                selected_youtube_url,
                download_dir,
                progress_callback=update_download_status,
            )
            st.session_state.downloaded_source_path = str(main_video_path.resolve())
            status_box.info(f"Downloaded to: {main_video_path.resolve()}")
        else:
            status_box.info("Saving uploaded video...")
            main_video_path = save_uploaded_file(main_upload, input_dir)

        intro_path = (
            save_uploaded_file(intro_upload, input_dir)
            if include_intro and intro_upload
            else None
        )
        outro_path = (
            save_uploaded_file(outro_upload, input_dir)
            if include_outro and outro_upload
            else None
        )

        output_paths = split_and_stitch_video(
            video_path=main_video_path,
            timestamp_text=timestamp_text,
            intro_path=intro_path,
            outro_path=outro_path,
            output_dir=output_dir,
            progress_callback=update_render_progress,
        )

        results = []
        for output_path in output_paths:
            results.append((output_path.name, output_path.read_bytes()))

        st.session_state.clip_results = results
        st.session_state.zip_results = build_zip(results)
        st.session_state.last_output_dir = str(output_dir.resolve())
        progress_bar.progress(100)
        status_box.success("Clips created.")
    except Exception as error:
        progress_bar.empty()
        status_box.error(str(error))


if st.session_state.clip_results:
    st.subheader("Downloads")
    if st.session_state.downloaded_source_path:
        st.info(f"YouTube source saved at: {st.session_state.downloaded_source_path}")
    if st.session_state.last_output_dir:
        st.info(f"Rendered clips saved at: {st.session_state.last_output_dir}")

    st.download_button(
        "Download all clips as ZIP",
        data=st.session_state.zip_results,
        file_name="video_chapters.zip",
        mime="application/zip",
    )

    for file_name, file_bytes in st.session_state.clip_results:
        st.download_button(
            file_name,
            data=file_bytes,
            file_name=file_name,
            mime="video/mp4",
        )
