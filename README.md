# Video Clipper

Local Streamlit application that creates timestamped chapters, optionally adding a normalized intro and outro to every chapter. It runs FFmpeg on the local machine; videos are not sent to an application server.

## Setup

Install a current full [FFmpeg build](https://ffmpeg.org/download.html) so both `ffmpeg` and `ffprobe` are on `PATH`, then create an environment and install the Python dependencies:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt  # Windows: .venv\\Scripts\\pip.exe install -r requirements.txt
.venv/bin/streamlit run app.py             # Windows: .venv\\Scripts\\streamlit.exe run app.py
```

The server accepts uploads up to 4 GB by default (`.streamlit/config.toml`). That is a configurable safety limit, not a promise that every machine can process every file.

## Reliability model

- FFmpeg, rather than MoviePy, decodes and encodes video. Each chapter is a separate invocation with a persistent `*.ffmpeg.log` file.
- A local background job saves state in `streamlit_workdir/jobs/<job-id>/job.json`; browser refreshes do not terminate rendering.
- Uploads, outputs, and ZIP archives stay on disk. The UI does not build a second in-memory list of all rendered files.
- Jobs check available storage before rendering. Keep at least several times the total input size free for source copies, temporary files, output, and a ZIP.
- Output is written to a `.partial.mp4` file and renamed only after FFmpeg succeeds.
- The **Fast cuts** option uses stream copy for keyframe-aligned cuts; leave it off for frame-accurate cuts or when using intro/outro.
- Completed and failed job directories are removed after seven days. Copy any files you need before then.

## Troubleshooting

If a job fails, open its job directory shown in the UI and inspect the adjacent `.ffmpeg.log` file. The UI also displays FFmpeg's final error and Python traceback. On Windows, prevent sleep while encoding and ensure antivirus exclusions do not quarantine the working directory.

For reproducible installations, record the Python and FFmpeg versions used by a successful test machine. Test real long media, low-disk conditions, interruption/restart behavior, no-audio input, and all target codecs before deploying to colleagues.
