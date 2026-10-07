import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import playlist_sources as sources
import webapp


class FeaturedArtistMatchingTests(unittest.TestCase):
    wanted = {"title": "Buggin' (feat. Jem Cooke)", "artist": "Hot Since 82", "duration": 505}
    original = {"title": "Buggin'", "artist": "Hot Since 82, Jem Cooke", "duration": "8:25"}

    def test_featured_credit_can_move_between_title_and_artist(self):
        for marker in ("feat.", "ft.", "featuring", "FEAT", "Ft."):
            for title in (f"Buggin' ({marker} Jem Cooke)", f"Buggin' [{marker} Jem Cooke]", f"Buggin' {marker} Jem Cooke"):
                for candidate in (self.original, {**self.original, "artist": "Hot Since 82"}):
                    with self.subTest(title=title, artist=candidate["artist"]):
                        self.assertEqual(sources.matching_track({**self.wanted, "title": title}, [candidate]), candidate)

    def test_reverse_placement_and_credit_in_artist(self):
        self.assertEqual(sources.matching_track(self.original, [self.wanted]), self.wanted)
        wanted = {**self.wanted, "title": "Buggin'", "artist": "Hot Since 82 feat. Jem Cooke"}
        self.assertEqual(sources.matching_track(wanted, [self.original]), self.original)

    def test_rejects_wrong_lead_artist_guests_versions_and_duration(self):
        for changes in ({"artist": "Another Artist, Jem Cooke"},
                        {"artist": "Jem Cooke"},
                        {"artist": "Hot Since 82, Other Singer"},
                        {"title": "Buggin' (feat. Other Singer)"},
                        {"mix": "Jorhav Remix"}, {"mix": "Dub"},
                        {"title": "Buggin' (feat. Jem Cooke) (Club Remix)"},
                        {"title": "Buggin' feat. Jem Cooke - Club Remix"},
                        {"title": "Buggin' (feat. Jem Cooke Remix)"},
                        {"duration": "4:35"}):
            with self.subTest(changes=changes):
                self.assertIsNone(sources.matching_track(self.wanted, [{**self.original, **changes}]))

    def test_keeps_version_when_it_matches(self):
        wanted = {**self.wanted, "title": "Buggin' (feat. Jem Cooke) (Club Remix)"}
        candidate = {**self.original, "mix": "Club Remix"}
        self.assertEqual(sources.matching_track(wanted, [candidate]), candidate)

    def test_prefers_closest_known_duration_not_first_result(self):
        candidates = [{**self.original, "duration": ""}, {**self.original, "duration": "8:20"}, self.original]
        self.assertIs(sources.matching_track(self.wanted, candidates), self.original)
        self.assertEqual(sources.matching_track({**self.wanted, "duration": "8:25"}, [self.original]), self.original)

    def test_missing_radio_edit_label_requires_near_identical_duration(self):
        short = {**self.original, "mix": "Radio Edit", "duration": "4:35"}
        self.assertEqual(sources.matching_track({**self.wanted, "duration": 275}, [short]), short)
        edit = {**short, "mix": "Edit"}
        self.assertEqual(sources.matching_track({**self.wanted, "duration": 275}, [edit]), edit)
        named_edit = {**short, "mix": "Another Artist Edit"}
        self.assertIsNone(sources.matching_track({**self.wanted, "duration": 275}, [named_edit]))
        for duration in (None, 505, 280):
            self.assertIsNone(sources.matching_track({**self.wanted, "duration": duration}, [short]))
        wanted = {**self.wanted, "title": "Buggin' (feat. Jem Cooke) (Extended Mix)", "duration": 275}
        self.assertIsNone(sources.matching_track(wanted, [short]))

    def test_beatport_duration_rejects_long_version_for_short_track(self):
        for length in ({"length": "8:25"}, {"length_ms": 505545}):
            candidate = webapp.normalize_beatport_results({"tracks": [{"id": 10726480, "name": "Buggin'",
                "artists": [{"name": "Hot Since 82"}, {"name": "Jem Cooke"}], **length}]})[0]
            self.assertEqual(candidate["duration"], "8:25")
            self.assertEqual(sources.matching_track(self.wanted, [candidate]), candidate)
            self.assertIsNone(sources.matching_track({**self.wanted, "duration": 275}, [candidate]))

    def test_does_not_relax_unrelated_artist_comparisons(self):
        self.assertIsNone(sources.matching_track({"title": "Song", "artist": "Artist"},
            [{"title": "Song", "artist": "Artist, Unrelated Singer"}]))

    def test_sources_preserve_separate_version_metadata(self):
        tidal = {"id": 123, "title": "Buggin'", "version": "Club Remix", "artists": [{"name": "Hot Since 82"}],
                 "duration": 505, "allowStreaming": True, "streamReady": True}
        qobuz = {"id": 123, "title": "Buggin'", "version": "Club Remix", "performer": {"name": "Hot Since 82"}, "duration": 505}
        for normalize, track in ((webapp.normalize_tidal_results, tidal), (webapp.normalize_qobuz_results, qobuz)):
            candidate = normalize({"tracks": {"items": [track]}})[0]
            self.assertEqual(candidate["mix"], "Club Remix")
            self.assertIsNone(sources.matching_track(self.wanted, [candidate]))
