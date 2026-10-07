import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_playlist_tests as runner


def payload(source):
    return {"source": source, "name": "Playlist", "url": "https://example.org", "total": 1,
            "tracks": [{"number": 1, "title": "Titre", "artist": "Artiste", "duration_seconds": 180,
                        "url": "", "unavailable": False}]}


class PlaylistTestRunnerTests(unittest.TestCase):
    def test_six_exports_and_report(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
            reader = mock.Mock(side_effect=[payload(source) for source, _, _ in runner.CASES])
            result = runner.run_tests(Path(root), reader)
            self.assertEqual(result["passed"], 6)
            self.assertEqual(reader.call_args_list, [mock.call(url) for _, _, url in runner.CASES])
            self.assertEqual(len(list(Path(root).iterdir())), 13)
            self.assertEqual([r["kind"] for r in result["results"]], ["playlist"] * 3 + ["album"] * 3)
            self.assertEqual(json.loads((Path(root) / "report.json").read_text()), result)

    def test_failure_does_not_skip_other_playlists_or_leak_details(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
            reader = mock.Mock(side_effect=[payload("spotify"), RuntimeError("secret-token"), payload("soundcloud")]
                               + [payload(source) for source, _ in runner.ALBUMS])
            result = runner.run_tests(Path(root), reader)
            self.assertEqual(result["passed"], 5)
            self.assertEqual(reader.call_count, 6)
            self.assertFalse((Path(root) / "applemusic.txt").exists())
            self.assertNotIn("secret-token", json.dumps(result))

    def test_cli_creates_unique_runs_and_reports_failure_exit_code(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()), \
             mock.patch.object(runner.export_playlist, "read_with_docker", side_effect=ValueError("Inaccessible")) as read:
            for _ in range(2):
                self.assertEqual(runner.main(["--docker", "--output-dir", root]), 1)
            self.assertEqual(len(list(Path(root).iterdir())), 2)
            self.assertEqual(read.call_count, 12)

    def test_rejects_incomplete_payload(self):
        item = payload("spotify")
        item["total"] = 166
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
            result = runner.run_tests(Path(root), mock.Mock(side_effect=[item, payload("applemusic"), payload("soundcloud")]
                                     + [payload(source) for source, _ in runner.ALBUMS]))
            self.assertEqual(result["passed"], 5)
            self.assertFalse((Path(root) / "spotify.txt").exists())
