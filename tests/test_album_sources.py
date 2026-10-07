import base64
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import export_playlist
import playlist_sources as sources
import test_playlist_sources


SPOTIFY_ID = "4m2880jivSbbyEGAKfITCa"
SPOTIFY_URL = f"https://open.spotify.com/album/{SPOTIFY_ID}"
APPLE_URL = "https://music.apple.com/us/album/random-access-memories/617154241"


def spotify_page(count=13, total=None, offset=0):
    tracks = [{"track": {"id": str(n), "name": f"Track {n}",
                         "artists": {"items": [{"profile": {"name": "Artist"}}]},
                         "duration": {"totalMilliseconds": 180001}}} for n in range(count)]
    state = {"entities": {"items": {f"spotify:album:{SPOTIFY_ID}": {
        "name": "Album", "tracksV2": {"totalCount": count if total is None else total,
                                       "pagingInfo": {"offset": offset}, "items": tracks}}}}}
    encoded = base64.b64encode(json.dumps(state).encode()).decode()
    return f'<script id="initialState">{encoded}</script>'


class AlbumSourceTests(unittest.TestCase):
    def test_album_urls_and_track_url_rejection(self):
        self.assertEqual(sources.playlist_source(SPOTIFY_URL), ("spotify", SPOTIFY_ID, SPOTIFY_URL))
        self.assertEqual(sources.playlist_source(SPOTIFY_URL.replace("/album/", "/intl-fr/album/") + "?si=abc")[2], SPOTIFY_URL)
        self.assertEqual(sources.playlist_source(APPLE_URL)[1], "617154241")
        self.assertEqual(sources.playlist_source("https://music.apple.com/us/album/617154241")[1], "617154241")
        for url in (APPLE_URL + "?l=fr&i=617154353", APPLE_URL + "?i=", SPOTIFY_URL.replace(".com/", ".com.evil/")):
            with self.subTest(url=url), self.assertRaises(sources.PlaylistError):
                sources.playlist_source(url)

    def test_spotify_album_order_duration_and_no_count_cap(self):
        result = sources.spotify_album_metadata(spotify_page(1201), SPOTIFY_ID)
        self.assertEqual(result["total"], 1201)
        self.assertEqual(result["tracks"][0]["duration"], 180.001)
        self.assertEqual(result["tracks"][-1]["title"], "Track 1200")

    def test_spotify_refuses_incomplete_wrong_or_invalid_album(self):
        for html in (spotify_page(10, 13), spotify_page(0), spotify_page(offset=1),
                     spotify_page().replace("initialState", "unrelated"),
                     '<script id="initialState">invalid!</script>'):
            with self.subTest(html=html[:70]), self.assertRaises(sources.PlaylistError):
                sources.spotify_album_metadata(html, SPOTIFY_ID)
        with self.assertRaises(sources.PlaylistError):
            sources.spotify_album_metadata(spotify_page(), "other")

    def test_apple_album_uses_same_ordered_metadata_contract(self):
        fixture = test_playlist_sources.PlaylistSourceTests()
        html = fixture.apple_page(13).replace(fixture.apple_id, "617154241").replace(
            "PlaylistDetailPageIntent", "AlbumDetailPageIntent")
        result = sources.apple_music_metadata(html, "617154241")
        self.assertEqual(result["total"], 13)
        self.assertEqual(len(result["tracks"]), 13)
        with self.assertRaises(sources.PlaylistError):
            sources.apple_music_metadata(html.replace('"trackCount": 13', '"trackCount": 14'), "617154241")

    def test_export_and_site_use_album_reader_without_playlist_search(self):
        import webapp
        with mock.patch.object(sources, "get_page", return_value=mock.Mock(text=spotify_page())), \
             mock.patch.object(sources, "spotify_playlist", side_effect=AssertionError("Not a playlist")):
            result = export_playlist.read_playlist(SPOTIFY_URL)
            self.assertEqual(result["total"], 13)
            self.assertEqual(result["tracks"][0]["number"], 1)
            self.assertEqual(webapp.read_import_source(SPOTIFY_URL)["total"], 13)


if __name__ == "__main__":
    unittest.main()
