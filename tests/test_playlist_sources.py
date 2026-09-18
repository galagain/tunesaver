import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import playlist_sources as sources


class PlaylistSourceTests(unittest.TestCase):
    apple_id = "pl.u-EdAVz8eCqqRzE2b"
    apple_url = "https://music.apple.com/fr/playlist/melodic-techno/pl.u-EdAVz8eCqqRzE2b"

    def apple_page(self, count=2, total=None):
        header = {"title": "Melodic techno", "trackCount": count if total is None else total,
                  "contentDescriptor": {"identifiers": {"storeAdamID": self.apple_id}}}
        tracks = [{"title": "Sarabande (Christian Löffler Remix)", "artistName": "Christian Prommer",
                   "duration": 463389, "contentDescriptor": {"kind": "song"}} for _ in range(count)]
        sections = [{"itemKind": "containerDetailHeaderLockup", "items": [header]},
                    {"id": f"track-list - {self.apple_id}", "itemKind": "trackLockup", "items": tracks},
                    {"id": "recommendations", "itemKind": "trackLockup", "items": tracks[:1]}]
        data = {"data": [{"intent": {"$kind": "PlaylistDetailPageIntent",
                 "contentDescriptor": {"identifiers": {"storeAdamID": self.apple_id}}},
                 "data": {"sections": sections}}]}
        return '<script id="serialized-server-data" type="application/json">' + json.dumps(data, ensure_ascii=False) + '</script>'

    def test_apple_urls(self):
        for suffix in ("", "?l=en&ls=1", "/"):
            self.assertEqual(sources.playlist_source(self.apple_url + suffix),
                             ("applemusic", self.apple_id, self.apple_url))
        self.assertEqual(sources.playlist_source("https://music.apple.com/us/playlist/pl.abc123")[1], "pl.abc123")
        for url in ("https://music.apple.com.evil.test/fr/playlist/pl.abc",
                    "https://music.apple.com/fr/album/pl.abc",
                    "https://music.apple.com:8080/fr/playlist/pl.abc",
                    "https://music.apple.com/fr/playlist/plXabc"):
            with self.subTest(url=url), self.assertRaises(sources.PlaylistError):
                sources.playlist_source(url)

    def test_apple_metadata_preserves_versions_durations_order_and_duplicates(self):
        result = sources.apple_music_metadata(self.apple_page(150), self.apple_id)
        self.assertEqual(result["total"], 150)
        self.assertEqual(len(result["tracks"]), 150)
        self.assertEqual(result["name"], "Melodic techno")
        self.assertEqual(result["tracks"][0], {"title": "Sarabande (Christian Löffler Remix)",
                         "artist": "Christian Prommer", "duration": 463.389})

    def test_apple_rejects_partial_empty_oversized_or_invalid_pages(self):
        for page in (self.apple_page(2, 5), self.apple_page(0), self.apple_page(1, 501),
                     self.apple_page().replace('"artistName": "Christian Prommer"', '"artistName": ""'),
                     self.apple_page().replace('"kind": "song"', '"kind": "music-video"'),
                     "<html>Private playlist</html>", '<script id="serialized-server-data">broken</script>'):
            with self.subTest(page=page[:80]), self.assertRaises(sources.PlaylistError):
                sources.apple_music_metadata(page, self.apple_id)

    def test_apple_redirects_are_validated_before_fetch_and_accents_preserved(self):
        response = sources.requests.Response()
        response.status_code = 200
        response._content = self.apple_page().encode("utf-8")
        response.encoding = "ISO-8859-1"
        redirect = mock.Mock(status_code=301, headers={"Location": self.apple_url})
        with mock.patch.object(sources.requests, "get", side_effect=[redirect, response]) as get:
            result = sources.apple_music_playlist("https://music.apple.com/fr/playlist/" + self.apple_id)
            self.assertIn("Löffler", result["tracks"][0]["title"])
            self.assertEqual(get.call_count, 2)
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
        for target in ("https://localhost/private", "https://music.apple.com/fr/playlist/pl.other"):
            with mock.patch.object(sources.requests, "get", return_value=mock.Mock(
                    status_code=302, headers={"Location": target})) as get:
                with self.assertRaises(sources.PlaylistError):
                    sources.apple_music_playlist(self.apple_url)
                get.assert_called_once()

    def test_urls_are_canonical_and_restrict_external_hosts(self):
        self.assertEqual(sources.playlist_source("https://open.spotify.com/intl-fr/playlist/3cEYpjA9oz9GiPac4AsH4n?si=abc")[2],
                         "https://open.spotify.com/playlist/3cEYpjA9oz9GiPac4AsH4n")
        self.assertEqual(sources.playlist_source("https://www.beatport.com/playlists/123")[1], "playlist:123")
        self.assertEqual(sources.playlist_source("https://www.beatport.com/chart/example/123")[1], "chart:123")
        self.assertEqual(sources.playlist_source("https://soundcloud.com/artist/sets/house")[0], "soundcloud")
        for url in ("http://localhost/playlist/1", "https://open.spotify.com.evil.test/playlist/123",
                    "https://user:pass@soundcloud.com/a/sets/b", "https://127.0.0.1/a/sets/b",
                    "https://soundcloud.com:8080/a/sets/b", "https://soundcloud.com/a/track", None):
            with self.subTest(url=url), self.assertRaises(sources.PlaylistError):
                sources.playlist_source(url)

    @mock.patch.dict(sources.os.environ, {}, clear=True)
    def test_spotify_uses_browser_without_api_credentials(self):
        payload = {"name": "Playlist", "total": 148, "tracks": [{"title": "Song"}] * 148}
        process = mock.Mock(pid=1234, returncode=0)
        process.communicate.return_value = (json.dumps(payload), None)
        with mock.patch.object(sources.subprocess, "Popen", return_value=process) as popen, mock.patch.object(sources.os, "killpg") as kill:
            result = sources.spotify_browser_playlist("57HAIqxdHbi332994xcOKc")
            self.assertEqual(len(result["tracks"]), 148)
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["HOME"], env["XDG_CONFIG_HOME"])
            self.assertEqual(env["HOME"], env["XDG_STATE_HOME"])
            kill.assert_called_once_with(1234, sources.signal.SIGKILL)

    @mock.patch.dict(sources.os.environ, {}, clear=True)
    def test_spotify_hybrid_preview_boundary_and_browser_fallback(self):
        for count in (5, 99, 100, 148):
            with self.subTest(count=count):
                entity = {"name": "Preview", "trackList": [
                    {"entityType": "track", "title": "Song", "subtitle": "Artist", "duration": 180000}
                ] * count}
                data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
                response = mock.Mock(text='<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>')
                with mock.patch.object(sources, "get_page", return_value=response), mock.patch.object(
                    sources, "spotify_browser_playlist", return_value={"name": "Browser"}
                ) as browser:
                    result = sources.spotify_playlist("57HAIqxdHbi332994xcOKc")
                    if count < 100:
                        browser.assert_not_called()
                        self.assertEqual(result["total"], count)
                        self.assertEqual(result["tracks"][0]["duration"], 180)
                    else:
                        browser.assert_called_once()
                        self.assertEqual(result["name"], "Browser")

    @mock.patch.dict(sources.os.environ, {}, clear=True)
    def test_spotify_unavailable_preview_falls_back(self):
        for response in (mock.Mock(text="not json"), sources.PlaylistError("Unavailable")):
            with mock.patch.object(sources, "get_page", side_effect=[response]), mock.patch.object(
                sources, "spotify_browser_playlist", return_value={"name": "Browser"}
            ) as browser:
                self.assertEqual(sources.spotify_playlist("57HAIqxdHbi332994xcOKc")["name"], "Browser")
                browser.assert_called_once()

    def test_spotify_browser_timeout_cleans_process_group(self):
        process = mock.Mock(pid=1234)
        process.communicate.side_effect = sources.subprocess.TimeoutExpired("browser", 160)
        with mock.patch.object(sources.subprocess, "Popen", return_value=process), mock.patch.object(sources.os, "killpg") as kill:
            with self.assertRaises(sources.PlaylistError):
                sources.spotify_browser_playlist("57HAIqxdHbi332994xcOKc")
            kill.assert_called_once()
            process.wait.assert_called_once()

    @mock.patch.dict(sources.os.environ, {"TUNESAVER_SPOTIFY_CLIENT_ID": "id", "TUNESAVER_SPOTIFY_CLIENT_SECRET": "secret"}, clear=True)
    def test_spotify_api_paginates(self):
        token = mock.Mock()
        token.json.return_value = {"access_token": "token"}
        def response(data):
            result = mock.Mock()
            result.json.return_value = data
            return result
        track = {"type": "track", "name": "Song", "artists": [{"name": "Artist"}], "duration_ms": 180000}
        with mock.patch.object(sources.requests, "post", return_value=token), mock.patch.object(sources, "get_page", side_effect=[
            response({"name": "Favorites"}), response({"items": [{"track": track}], "next": "next", "total": 2}),
            response({"items": [{"item": track}], "next": None, "total": 2}),
        ]) as get:
            self.assertEqual(len(sources.spotify_playlist("id")["tracks"]), 2)
            self.assertEqual(get.call_args.kwargs["params"]["offset"], 1)

    def test_soundcloud_extraction_never_downloads_audio(self):
        metadata = {"id": 1, "title": "Artist - Song", "user": {"username": "Label"}, "duration": 180000}
        fetch = mock.Mock(return_value=[metadata])
        result = sources.soundcloud_metadata({"title": "Set", "tracks": [{"id": 1}]}, fetch)
        self.assertEqual(result["tracks"][0]["artist"], "Artist")
        self.assertEqual(result["tracks"][0]["duration"], 180)
        fetch.assert_called_once_with(["1"])

    def test_match_requires_title_artist_version_and_duration(self):
        wanted = {"title": "Song", "artist": "Artist", "duration": 180}
        good = {"title": "Song", "artist": "Artist", "duration": "3:00", "mix": "Original Mix"}
        self.assertEqual(sources.matching_track(wanted, [good]), good)
        for update in ({"artist": "Other"}, {"title": "Another Song"}, {"mix": "Club Remix"}, {"duration": "6:00"}):
            self.assertIsNone(sources.matching_track(wanted, [{**good, **update}]))
        self.assertIsNone(sources.matching_track({"title": "Song"}, [good]))
