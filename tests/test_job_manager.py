import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_manager import ChapterJob


class ChapterJobStatusFileTest(unittest.TestCase):
    def test_read_retries_a_windows_sharing_violation(self):
        with tempfile.TemporaryDirectory() as directory:
            job = ChapterJob(Path(directory))
            job.status_path.write_text(json.dumps({"state": "running"}), encoding="utf-8")
            original_read_text = Path.read_text
            attempts = 0

            def flaky_read(path, *args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError("file is temporarily in use")
                return original_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", flaky_read), patch("job_manager.time.sleep"):
                self.assertEqual(job.read()["state"], "running")
            self.assertEqual(attempts, 2)


if __name__ == "__main__":
    unittest.main()
