import unittest
from pathlib import Path
from unittest.mock import patch

from video_processing import VideoProcessingError, _audio_filter, _video_filter, ensure_free_space, required_free_space


class VideoProcessingTest(unittest.TestCase):
    def test_filters_trim_and_normalize(self):
        video = _video_filter(0, "source", 1920, 1080, 12, 20)
        audio = _audio_filter(0, "source", 8, True, 12, 20)
        self.assertIn("trim=start=12.000000:end=20.000000", video)
        self.assertIn("scale=1920:1080", video)
        self.assertIn("atrim=start=12.000000:end=20.000000", audio)

    def test_silent_source_gets_generated_audio(self):
        audio = _audio_filter(0, "source", 8, False)
        self.assertIn("anullsrc", audio)
        self.assertIn("duration=8.000000", audio)

    def test_space_estimate_includes_working_copies(self):
        with patch.object(Path, "stat") as stat:
            stat.return_value.st_size = 100
            self.assertEqual(required_free_space(["source.mp4"]), 300)

    def test_low_disk_space_has_actionable_error(self):
        with patch("video_processing.required_free_space", return_value=100), patch("video_processing.shutil.disk_usage") as disk:
            disk.return_value.free = 10
            with self.assertRaisesRegex(VideoProcessingError, "Insufficient disk space"):
                ensure_free_space(".", ["source.mp4"])


if __name__ == "__main__":
    unittest.main()
