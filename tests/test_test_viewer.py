import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from build_test_viewer import build_viewer


class TestViewerTests(unittest.TestCase):
    def test_embeds_success_and_failure_without_script_injection(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            payload = {"name": "</script><script>alert(1)</script>", "tracks": []}
            (directory / "spotify.json").write_text(json.dumps(payload))
            results = [{"source": "spotify", "kind": "playlist", "status": "ok", "files": ["spotify.json"]},
                       {"source": "qobuz", "kind": "album", "status": "failed", "error": "Indisponible"}]
            (directory / "report.json").write_text(json.dumps({"created_at": "2026-10-07", "results": results}))
            output = build_viewer(directory).read_text()
            self.assertNotIn("</script><script>alert", output)
            self.assertIn("\\u003c/script\\u003e", output)
            self.assertIn('"status": "failed"', output)
            self.assertNotIn("/* VIEWER_", output)
            self.assertNotIn("export const Search", output)
            self.assertEqual(build_viewer(directory).read_text(), output)

    def test_refuses_report_file_outside_run(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            (directory / "report.json").write_text(json.dumps({"created_at": "2026-10-07", "results": [
                {"status": "ok", "files": ["../outside.json"]}]}))
            with self.assertRaises(ValueError):
                build_viewer(directory)
