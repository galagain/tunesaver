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


def payload(source, kind="playlist"):
    native = source in {"tiddl", "beatport", "qobuz"}
    return {"schema_version": 1, "kind": kind, "source": source, "name": "Playlist", "url": "https://example.org", "total": 1,
            "tracks": [{"number": 1, "title": "Titre", "artist": "Artiste", "duration_seconds": 180,
                        "url": "https://example.org/track/1", "unavailable": False, "mix": "", "album": "",
                        "requires_matching": not native,
                        "target": {"provider": "tidal" if source == "tiddl" else source, "kind": "track",
                                   "method": "direct", "url": "https://example.org/track/1"} if native else None}]}


class PlaylistTestRunnerTests(unittest.TestCase):
    def test_all_exports_and_report(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
            reader = mock.Mock(side_effect=[payload(source, kind) for source, kind, _ in runner.CASES])
            result = runner.run_tests(Path(root), reader)
            self.assertEqual(result["passed"], len(runner.CASES))
            self.assertEqual(reader.call_args_list, [mock.call(url) for _, _, url in runner.CASES])
            self.assertEqual(len(list(Path(root).iterdir())), 2 * len(runner.CASES) + 2)
            self.assertEqual([r["kind"] for r in result["results"]], [kind for _, kind, _ in runner.CASES])
            self.assertEqual(len(json.loads((Path(root) / "inputs.json").read_text())["collections"]), len(runner.CASES))
            self.assertEqual(json.loads((Path(root) / "report.json").read_text()), result)

    def test_failure_does_not_skip_other_playlists_or_leak_details(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
            reader = mock.Mock(side_effect=[payload("spotify"), RuntimeError("secret-token"), payload("soundcloud")]
                               + [payload(source, kind) for source, kind, _ in runner.CASES[3:]])
            result = runner.run_tests(Path(root), reader)
            self.assertEqual(result["passed"], len(runner.CASES) - 1)
            self.assertEqual(reader.call_count, len(runner.CASES))
            self.assertFalse((Path(root) / "applemusic.txt").exists())
            self.assertNotIn("secret-token", json.dumps(result))

    def test_cli_creates_unique_runs_and_reports_failure_exit_code(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()), \
             mock.patch.object(runner.export_playlist, "read_with_docker", side_effect=ValueError("Inaccessible")) as read:
            for _ in range(2):
                self.assertEqual(runner.main(["--docker", "--output-dir", root]), 1)
            self.assertEqual(len(list(Path(root).iterdir())), 2)
            self.assertEqual(read.call_count, 2 * len(runner.CASES))

    def test_rejects_incomplete_payload(self):
        item = payload("spotify")
        item["total"] = 166
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
            result = runner.run_tests(Path(root), mock.Mock(side_effect=[item, payload("applemusic"), payload("soundcloud")]
                                     + [payload(source, kind) for source, kind, _ in runner.CASES[3:]]))
            self.assertEqual(result["passed"], len(runner.CASES) - 1)
            self.assertFalse((Path(root) / "spotify.txt").exists())

    def test_native_targets_and_collection_kind_are_required(self):
        for source in ("tiddl", "beatport", "qobuz"):
            item = payload(source, "track")
            item["tracks"][0]["target"] = None
            with self.assertRaises(ValueError):
                runner.validate(item, source, "track")
        with self.assertRaises(ValueError):
            runner.validate(payload("spotify", "album"), "spotify", "track")
