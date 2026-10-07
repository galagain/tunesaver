import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import match_music
from catalog_matcher import Catalog, MatchError, match_album, match_collection, read_input, match_track, public_track, direct_target
import playlist_sources


def track(title="Song", artist="Artist", **extra):
    return {"title": title, "artist": artist, "duration": 180, "source_url": "https://example.org/track", **extra}


def collection(kind="playlist", tracks=None):
    return {"name": "Album", "kind": kind, "tracks": tracks if tracks is not None else [track()]}


class CatalogMatcherTests(unittest.TestCase):
    def test_soundcloud_decorations_are_normalized_for_cached_inputs(self):
        original = track('PREMIERE: Kareem Ali - On My Heart [Noire & Blanche]', 'Kareem Ali')
        catalog = mock.Mock()
        catalog.search_tracks.return_value = [track('On My Heart', 'Kareem Ali')]
        source = {**collection(tracks=[original]), 'source': 'https://soundcloud.com/label/sets/jazz'}
        result = match_collection(source, catalog)
        self.assertEqual(result['matched'], 1)
        self.assertEqual(catalog.search_tracks.call_args.args[1], 'Kareem Ali On My Heart')
        self.assertEqual(original['title'], 'PREMIERE: Kareem Ali - On My Heart [Noire & Blanche]')

    def test_soundcloud_normalization_is_source_specific(self):
        catalog = mock.Mock()
        catalog.search_tracks.return_value = [track('Song')]
        source = {**collection(tracks=[track('Artist - Song')]), 'source': 'https://open.spotify.com/playlist/id'}
        self.assertEqual(match_collection(source, catalog)['matched'], 0)

    def test_soundcloud_metadata_cleans_publisher_and_uploader_titles(self):
        for publisher in ({'artist': 'Flying Moth'}, {}):
            entry = {'id': 1, 'title': 'Flying Moth - Girl, I Need You', 'publisher_metadata': publisher,
                     'user': {'username': 'Label'}, 'duration': 274312}
            result = playlist_sources.soundcloud_metadata({'tracks': [entry]}, mock.Mock())['tracks'][0]
            self.assertEqual((result['title'], result['artist']), ('Girl, I Need You', 'Flying Moth'))
            self.assertEqual(result['duration'], 274.312)

    def test_soundcloud_preserves_editions_and_ambiguous_titles(self):
        normalize = playlist_sources.normalize_soundcloud_track
        for edition in ('Extended Mix', 'Club Remix', 'Radio Edit', 'Live', 'Dub', 'Remastered', 'Instrumental'):
            self.assertEqual(normalize(track(f'Artist - Song [{edition}]'))['title'], f'Song [{edition}]')
            self.assertEqual(normalize(track(f'Artist - Song ({edition}) [Label]'))['title'], f'Song ({edition})')
        self.assertEqual(normalize(track('Love - Hate'))['title'], 'Love - Hate')
        self.assertEqual(normalize(track('Song [Part Two]'))['title'], 'Song [Part Two]')
        self.assertEqual(normalize(track('Artist - Song - Extended Mix'))['title'], 'Song - Extended Mix')
        self.assertEqual(normalize(track('Tour-Maubourg - Dreams', 'Tour-Maubourg'))['artist'], 'Tour-Maubourg')

    def test_native_links_skip_all_matching_for_tracks_and_albums(self):
        for provider, url in [("tiddl", "https://listen.tidal.com/track/20115557"),
                              ("beatport", "https://www.beatport.com/track/give-life-back-to-music/4400239"),
                              ("qobuz", "https://play.qobuz.com/track/9140024")]:
            for kind in ("track", "album", "playlist"):
                catalog = mock.Mock()
                source = track(provider=provider, source_url=url)
                result = match_collection(collection(kind, [source, source] if kind != "track" else [source]), catalog)
                self.assertEqual(result["matched"], result["total"])
                self.assertTrue(all(row["match"]["method"] == "direct" for row in result["tracks"]))
                self.assertTrue(all(row["attempts"] == [] for row in result["tracks"]))
                catalog.search_tracks.assert_not_called()
                catalog.search_albums.assert_not_called()

    def test_direct_target_validates_host_and_kind(self):
        for url in ("https://evil.test/track/1", "http://tidal.com/track/1", "https://tidal.com/album/1",
                    "https://user@tidal.com/track/1", "https://tidal.com:8080/track/1"):
            self.assertIsNone(direct_target(track(provider="tidal", source_url=url)))
        self.assertIsNone(direct_target(track(provider="tidal", source_url="https://tidal.com/track/1", unavailable=True)))

    def test_priority_and_no_search_after_match(self):
        catalog = mock.Mock()
        catalog.search_tracks.side_effect = lambda p, q: [track()] if p == "beatport" else []
        result = match_collection(collection(), catalog)
        self.assertEqual(result["tracks"][0]["match"]["provider"], "beatport")
        self.assertEqual(list(dict.fromkeys(c.args[0] for c in catalog.search_tracks.call_args_list)), ["tidal", "beatport"])
        catalog.search_albums.assert_not_called()

    def test_album_first_then_tracks_for_each_provider(self):
        events = []
        class Fake:
            def search_albums(self, provider, source):
                events.append((provider, "album"))
                return []
            def search_tracks(self, provider, query):
                events.append((provider, "track"))
                return [track()] if provider == "qobuz" else []
        result = match_collection(collection("album"), Fake())
        self.assertEqual(list(dict.fromkeys(events)), [(p, k) for p in ("tidal", "beatport", "qobuz") for k in ("album", "track")])
        self.assertEqual(result["matched"], 1)

    def test_complete_album_avoids_individual_searches(self):
        catalog = mock.Mock()
        catalog.search_albums.return_value = [{"name": "Album", "url": "https://example.org/album", "tracks": [track()]}]
        result = match_collection(collection("album"), catalog)
        catalog.search_tracks.assert_not_called()
        self.assertEqual(catalog.search_albums.call_count, 1)
        self.assertEqual(result["tracks"][0]["match"]["method"], "album")

    def test_album_checks_edition_count_versions_and_durations(self):
        wanted = collection("album")
        for name, tracks in [("Album Deluxe", [track()]), ("Album", []),
                             ("Album", [track(mix="Remix")]), ("Album", [track(duration=300)])]:
            with self.subTest(name=name, tracks=tracks):
                self.assertIsNone(match_album(wanted, {"name": name, "tracks": tracks}))

    def test_duplicates_and_input_order_are_preserved(self):
        tracks = [track("Second"), track(), track()]
        catalog = mock.Mock()
        catalog.search_tracks.side_effect = lambda p, q: tracks
        result = match_collection(collection(tracks=tracks), catalog)
        self.assertEqual([r["number"] for r in result["tracks"]], [1, 2, 3])
        self.assertEqual([r["match"]["title"] for r in result["tracks"]], ["Second", "Song", "Song"])
        self.assertIsNone(match_album(collection("album", tracks), {"name": "Album", "tracks": [track("Second"), track(), track("Other")]}))

    def test_errors_do_not_leak_tokens_or_block_fallback(self):
        catalog = mock.Mock()
        catalog.search_tracks.side_effect = [RuntimeError("secret-token"), [], [], [track()]]
        result = match_collection(collection(), catalog)
        self.assertNotIn("secret-token", json.dumps(result))
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["tracks"][0]["match"]["provider"], "qobuz")

    def test_title_only_is_ambiguous_for_multiple_artists(self):
        found, ambiguous = match_track({"title": "Song"}, [track(), track(artist="Other")])
        self.assertIsNone(found)
        self.assertTrue(ambiguous)
        self.assertEqual(match_track({"title": "Song"}, [track()]), (track(), False))

    def test_featured_credits_in_version_field(self):
        wanted = track("Instant Crush (feat. Julian Casablancas)", "Daft Punk, Julian Casablancas")
        candidate = track("Instant Crush", "Daft Punk, Julian Casablancas", mix="feat. Julian Casablancas")
        self.assertIs(match_track(wanted, [candidate])[0], candidate)
        self.assertIsNone(match_track(wanted, [{**candidate, "mix": "Club Remix"}])[0])
        result = match_album(collection("album", [wanted]), {"name": "Album", "tracks": [candidate]})
        self.assertEqual(result, [candidate])

    def test_manual_input_and_invalid_links(self):
        catalog = mock.Mock()
        result = read_input("Hot Since 82 - Buggin' (feat. Jem Cooke)", catalog)
        self.assertEqual(result["tracks"][0]["artist"], "Hot Since 82")
        self.assertEqual(read_input("Buggin'", catalog)["tracks"][0]["artist"], "")
        for value in ("http://localhost/file", "https://example.org/track", "https://open.spotify.com.evil/track/123"):
            with self.subTest(value=value), self.assertRaises(MatchError):
                read_input(value, catalog)
        catalog.api.assert_not_called()

    def test_unavailable_tracks_are_not_searched(self):
        catalog = mock.Mock()
        result = match_collection(collection(tracks=[track(unavailable=True)]), catalog)
        self.assertEqual(result["tracks"][0]["status"], "unavailable")
        catalog.search_tracks.assert_not_called()

    def test_cli_file_and_atomic_report_without_database_or_audio(self):
        import webapp
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stderr(io.StringIO()), \
             mock.patch.object(webapp, "local_db", side_effect=AssertionError("database")), \
             mock.patch.object(webapp, "enqueue_import_track", side_effect=AssertionError("download")), \
             mock.patch.object(Catalog, "search_tracks", return_value=[track()]):
            inputs = Path(root) / "inputs.txt"
            inputs.write_text("# comment\nArtist - Song\n\nArtist - Song\n")
            output = Path(root) / "matches.json"
            self.assertEqual(match_music.main(["--file", str(inputs), "-o", str(output)]), 0)
            result = json.loads(output.read_text())
            self.assertFalse(result["audio_downloaded"])
            self.assertEqual(len(result["results"]), 2)
            self.assertEqual(match_music.main(["Artist - Song", "-o", str(output)]), 2)

    def test_native_album_normalization_has_no_ui_limit(self):
        catalog = Catalog()
        tracks = [{"id": n + 1, "title": f"Song {n}", "duration": 180,
                   "allowStreaming": True, "streamReady": True, "artists": [{"name": "Artist"}]} for n in range(105)]
        def api(provider, path, **params):
            if path.endswith("/tracks"):
                offset = params["offset"]
                return {"items": tracks[offset:offset + 100]}
            return {"title": "Album", "numberOfTracks": 105}
        with mock.patch.object(catalog, "api", side_effect=api):
            self.assertEqual(len(catalog.album("tidal", "1")["tracks"]), 105)

    def test_soundcloud_preview_uses_full_duration(self):
        item = {"id": 1, "title": "Song", "user": {"username": "Artist"}, "policy": "SNIP", "duration": 30000}
        for full, expected in [(275435, 275.435), (None, 0)]:
            result = playlist_sources.soundcloud_metadata({"tracks": [{**item, "full_duration": full}]}, mock.Mock())
            self.assertEqual(result["tracks"][0]["duration"], expected)

    def test_spotify_track_link_reads_entity_not_playlist(self):
        entity = {"title": "Song", "artists": [{"name": "Artist"}], "duration": 180000}
        payload = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
        html = '<script id="__NEXT_DATA__">' + json.dumps(payload) + '</script>'
        with mock.patch.object(playlist_sources, "get_page", return_value=mock.Mock(text=html)):
            self.assertEqual(public_track("https://open.spotify.com/track/0dEIca2nhcxDUV8C5QkPYb")["duration"], 180)

    def test_album_search_uses_main_artist_then_name_and_skips_bad_candidate(self):
        catalog = Catalog()
        with mock.patch.object(catalog, "api", return_value={"items": [
            {"id": 1, "title": "Album"}, {"id": 2, "title": "Album"}]} ) as api, \
             mock.patch.object(catalog, "album", side_effect=[MatchError("Unavailable"), {"name": "Album"}]):
            iterator = catalog.search_albums("tidal", collection("album"))
            self.assertEqual(next(iterator), {"name": "Album"})
            self.assertEqual(api.call_args.kwargs["query"], "Artist Album")
            with self.assertRaises(MatchError):
                list(iterator)
            self.assertEqual(api.call_args.kwargs["query"], "Album")

    def test_network_failure_is_cached_and_sanitized(self):
        import requests
        catalog = Catalog()
        with mock.patch.object(catalog, "_api", side_effect=requests.Timeout("secret-token")) as api:
            for _ in range(2):
                with self.assertRaises(MatchError) as error:
                    catalog.api("tidal", "search/tracks", query="Song")
                self.assertNotIn("secret-token", str(error.exception))
            self.assertEqual(api.call_count, 1)


if __name__ == "__main__":
    unittest.main()
