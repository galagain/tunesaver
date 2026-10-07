import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import run_matching_tests as runner
import catalog_matcher


def payload(source, kind):
    urls = {"tiddl": "https://tidal.com/track/1", "beatport": "https://www.beatport.com/track/song/1", "qobuz": "https://play.qobuz.com/track/1"}
    target = {"provider": "tidal" if source == "tiddl" else source, "kind": "track", "url": urls[source], "method": "direct"} if source in urls else None
    track = {"number": 1, "title": "Song", "artist": "Artist", "duration_seconds": 180, "url": urls.get(source, ""),
             "unavailable": False, "mix": "", "album": "", "target": target, "requires_matching": target is None}
    return {"schema_version": 1, "name": "Album", "source": source, "kind": kind, "url": "https://example.org", "total": 1, "tracks": [track]}


def candidate():
    return {"title": "Song", "artist": "Artist", "duration": 180, "provider": "tidal", "source_url": "https://tidal.com/track/2"}


class MatchingRunnerTests(unittest.TestCase):
    def test_all_19_configurations_without_reading_sources_or_audio(self):
        import webapp
        manifest = {"schema_version": 1, "collections": [payload(s, k) for s, k, _ in runner.CASES]}
        catalog = mock.Mock()
        catalog.search_tracks.return_value = [candidate()]
        catalog.search_albums.return_value = [{"name": "Album", "url": "https://tidal.com/album/2", "tracks": [candidate()]}]
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
             mock.patch.object(catalog_matcher, "read_input", side_effect=AssertionError("Source fetch forbidden")), \
             mock.patch.object(webapp, "local_db", side_effect=AssertionError("Database forbidden")), \
             mock.patch.object(webapp, "enqueue_import_track", side_effect=AssertionError("Audio forbidden")):
            report = runner.run_cases(manifest, Path(root), lambda p: runner.match_payload(p, catalog))
            self.assertEqual((report["passed"], report["tested"]), (19, 19))
            output = json.loads((Path(root) / "inputs.json").read_text())
            self.assertEqual(len(output["collections"]), 19)
            for result in output["collections"]:
                self.assertFalse(result["tracks"][0]["requires_matching"])
            self.assertTrue((Path(root) / "viewer.html").is_file())
        self.assertEqual(catalog.search_tracks.call_count, 7)
        self.assertEqual(catalog.search_albums.call_count, 3)

    def test_source_immutable_and_native_targets_preserved(self):
        original = payload("qobuz", "album")
        before = copy.deepcopy(original)
        catalog = mock.Mock()
        result = runner.match_payload(original, catalog)
        self.assertEqual(original, before)
        self.assertEqual(result["tracks"][0]["target"], original["tracks"][0]["target"])
        catalog.search_albums.assert_not_called()
        catalog.search_tracks.assert_not_called()

    def test_rejects_forged_native_target(self):
        item = payload("tiddl", "track")
        item["tracks"][0]["url"] = item["tracks"][0]["target"]["url"] = "https://evil.test/track/1"
        with self.assertRaises(ValueError):
            runner.match_payload(item, mock.Mock())

    def test_missing_scenario_and_no_match_are_not_successes(self):
        cases = [("spotify", "track", "url"), ("qobuz", "track", "url")]
        catalog = mock.Mock()
        catalog.search_tracks.return_value = []
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = runner.run_cases({"schema_version": 1, "collections": [payload("spotify", "track")]}, Path(root),
                                      lambda p: runner.match_payload(p, catalog), cases)
            self.assertEqual(result["passed"], 0)
            self.assertEqual(result["results"][0]["summary"]["not_found"], 1)
            self.assertEqual(result["results"][1]["status"], "failed")
            self.assertTrue((Path(root) / "viewer.html").is_file())

    def test_duplicate_entries_keep_order(self):
        item = payload("tiddl", "playlist")
        item["tracks"].append({**item["tracks"][0], "number": 2})
        item["total"] = 2
        result = runner.match_payload(item, mock.Mock())
        self.assertEqual([t["number"] for t in result["tracks"]], [1, 2])
        self.assertEqual(result["matching"]["summary"]["direct"], 2)

    def test_cli_selection_and_docker_transport(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
            source = Path(root) / "inputs.json"
            source.write_text(json.dumps({"schema_version": 1, "collections": [payload("tiddl", "track")]}))
            expected = runner.match_payload(payload("tiddl", "track"), mock.Mock())
            with mock.patch.object(runner.subprocess, "run", side_effect=[mock.Mock(returncode=0, stdout="image-id"),
                    mock.Mock(returncode=0, stdout=json.dumps(expected))]) as run:
                self.assertEqual(runner.main(["--input", str(source), "--docker", "--case", "tiddl-track"]), 0)
                self.assertEqual(json.loads(run.call_args.kwargs["input"])["source"], "tiddl")
                self.assertIn("--worker", run.call_args.args[0])
