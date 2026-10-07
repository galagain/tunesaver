import contextlib
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import export_playlist as export
import playlist_sources
import spotify_http
from test_spotify_browser import page


class PlaylistExportTests(unittest.TestCase):
    url = "https://open.spotify.com/playlist/57HAIqxdHbi332994xcOKc"

    def test_large_playlist_preserves_order_duplicates_and_durations(self):
        tracks = [{"title": "Same", "artist": "Artist", "duration": "3:21"}] * 1201
        with mock.patch.object(spotify_http, "read_playlist", return_value={"name": "Large", "total": 1201, "tracks": tracks}):
            result = export.read_playlist(self.url)
        self.assertEqual(result["total"], 1201)
        self.assertEqual(result["tracks"][-1]["number"], 1201)
        self.assertEqual(result["tracks"][0]["duration_seconds"], 201)

    def test_all_source_routes(self):
        import webapp
        cases = json.loads(Path(__file__).with_name("playlist_cases.json").read_text())
        payload = {"name": "Example", "total": 1, "tracks": [{"title": "Song", "artist": "Artist", "duration": 180}]}
        with mock.patch.object(spotify_http, "read_playlist", return_value=payload) as spotify, \
             mock.patch.object(playlist_sources, "apple_music_playlist", return_value=payload) as apple, \
             mock.patch.object(playlist_sources, "soundcloud_playlist", return_value=payload) as soundcloud, \
             mock.patch.object(webapp, "read_import_source", return_value=payload) as native:
            for case in cases:
                with self.subTest(source=case["source"]):
                    result = export.read_playlist(case["url"])
                    self.assertEqual(result["source"], case["source"])
                    self.assertEqual(result["total"], 1)
            self.assertEqual([spotify.call_count, apple.call_count, soundcloud.call_count, native.call_count], [1, 1, 1, 3])

    def test_formats_round_trip_and_txt_lines(self):
        with mock.patch.object(spotify_http, "read_playlist", return_value={"name": "Accent é", "tracks": [
                {"title": 'Title, "version"\nLive', "artist": "Artiste é", "duration": 123.4}]}):
            payload = export.read_playlist(self.url)
        self.assertEqual(json.loads(export.render(payload, "json")), payload)
        rows = list(csv.DictReader(io.StringIO(export.render(payload, "csv"))))
        self.assertEqual(rows[0]["title"], payload["tracks"][0]["title"])
        text = export.render(payload, "txt")
        self.assertIn('1. Artiste é - Title, "version" Live [2:03]', text)

    def test_cli_file_stdout_overwrite_and_incomplete_read(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stderr(io.StringIO()):
            path = Path(root) / "playlist.json"
            with mock.patch.object(spotify_http, "read_playlist", return_value={"name": "Empty", "total": 0, "tracks": []}):
                self.assertEqual(export.main([self.url, "-o", str(path)]), 0)
                self.assertEqual(json.loads(path.read_text())["tracks"], [])
                self.assertEqual(export.main([self.url, "-o", str(path)]), 1)
                self.assertEqual(export.main([self.url, "-o", str(path), "--force"]), 0)
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(export.main([self.url, "-o", "-", "--format", "json"]), 0)
                self.assertEqual(json.loads(out.getvalue())["total"], 0)
            original = path.read_text()
            with mock.patch.object(spotify_http, "read_playlist", return_value={"name": "Partial", "total": 10, "tracks": []}):
                self.assertEqual(export.main([self.url, "-o", str(path), "--force"]), 1)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(list(Path(root).glob(".playlist-*")), [])

    def test_atomic_write_does_not_clobber_existing_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "out.txt"
            path.write_text("original")
            with self.assertRaises(FileExistsError):
                export.write_output(path, "replacement")
            self.assertEqual(path.read_text(), "original")
            self.assertEqual(list(Path(root).iterdir()), [path])

    def test_invalid_durations(self):
        for value in (None, "", -1, "nan", "inf", "x:y"):
            self.assertIsNone(export.duration_seconds(value))
        self.assertEqual(export.duration_seconds("1:02:03"), 3723)

    def test_docker_mode_uses_running_image_and_no_shell(self):
        payload = {"name": "Fixture", "source": "spotify", "url": self.url, "total": 0, "tracks": []}
        with mock.patch.object(export.subprocess, "run", side_effect=[
                mock.Mock(returncode=0, stdout="sha256:fixture\n"),
                mock.Mock(returncode=0, stdout=json.dumps(payload))]) as run:
            result = export.read_with_docker(self.url, "tunesaver-web-1")
        self.assertEqual(result, payload)
        command = run.call_args.args[0]
        self.assertIn("sha256:fixture", command)
        self.assertIn(self.url, command)
        self.assertNotIn("--docker", command)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_docker_mode_forwards_only_sanitized_cli_errors(self):
        with mock.patch.object(export.subprocess, "run", side_effect=[
                mock.Mock(returncode=0, stdout="sha256:fixture\n"),
                mock.Mock(returncode=1, stderr="internal diagnostic\nErreur : HTTP 429 from open.spotify.com; no retry\n")]):
            with self.assertRaisesRegex(ValueError, "^HTTP 429 from open.spotify.com; no retry$"):
                export.read_with_docker(self.url, "tunesaver-web-1")

    def test_http_reader_paginates_without_browser(self):
        state = {"props": {"pageProps": {"state": {
            "settings": {"session": {"accessToken": "anonymous-fixture"}},
            "data": {"entity": {"name": "Playlist", "trackList": [{"title": f"Song {i}"} for i in range(100)]}}
        }}}}
        embed = mock.Mock(status_code=200, text='<script id="__NEXT_DATA__">' + json.dumps(state) + '</script>')
        html = mock.Mock(status_code=200, text='<script src="https://open.spotifycdn.com/cdn/build/web-player/web-player.abc123.js"></script>')
        bundle = mock.Mock(status_code=200, text='"fetchPlaylistContents","query","' + 'a' * 64 + '"')
        responses = [embed, html, bundle] + [mock.Mock(status_code=200, json=lambda offset=offset, count=count:
            page(offset, count, total=166)) for offset, count in [(0, 100), (100, 66)]]
        with mock.patch.object(spotify_http.requests, "Session") as session, \
             mock.patch("subprocess.Popen", side_effect=AssertionError("Browser forbidden")):
            session.return_value.__enter__.return_value.request.side_effect = responses
            result = spotify_http.read_playlist(self.url)
        self.assertEqual(result["total"], 166)
        self.assertEqual(result["tracks"][-1]["title"], "Song 165")
        self.assertEqual(result["requests"], 5)

    def test_http_rate_limit_stops_without_retry(self):
        with mock.patch.object(spotify_http.requests, "Session") as session:
            request = session.return_value.__enter__.return_value.request
            request.return_value.status_code = 429
            with self.assertRaisesRegex(ValueError, "HTTP 429"):
                spotify_http.read_playlist(self.url)
            request.assert_called_once()
