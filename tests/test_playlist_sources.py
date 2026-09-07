import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import playlist_sources as sources


class PlaylistSourceTests(unittest.TestCase):
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
    def test_spotify_embed_reads_titles_and_refuses_truncated_playlists(self):
        track = {"title": "Song", "subtitle": "Artist", "duration": 180000, "entityType": "track"}
        entity = {"name": "Favorites", "trackList": [track]}
        def page():
            return mock.Mock(text='<script id="__NEXT_DATA__">' + json.dumps({"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}) + '</script>')
        with mock.patch.object(sources, "get_page", side_effect=lambda *_a, **_k: page()):
            result = sources.spotify_playlist("3cEYpjA9oz9GiPac4AsH4n")
            self.assertEqual(result["tracks"][0]["duration"], 180)
            entity["trackList"] = [track] * 100
            with self.assertRaisesRegex(sources.PlaylistError, "100"):
                sources.spotify_playlist("3cEYpjA9oz9GiPac4AsH4n")

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
