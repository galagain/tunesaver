import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from spotify_browser import PlaylistPages


def page(offset, length, total=148):
    return {"data": {"playlistV2": {"name": "Test playlist", "content": {
        "totalCount": total, "pagingInfo": {"offset": offset}, "items": [
            {"uid": str(i), "itemV2": {"data": {"__typename": "Track", "name": f"Song {i}",
             "artists": {"items": [{"profile": {"name": "Artist"}}]},
             "trackDuration": {"totalMilliseconds": 180000}}}} for i in range(offset, offset + length)
        ]}}}}


class SpotifyBrowserTests(unittest.TestCase):
    def setUp(self):
        self.pages = PlaylistPages("id")
        self.request = {"operationName": "fetchPlaylistContents", "variables": {"uri": "spotify:playlist:id"}}

    def test_collects_148_in_order_despite_overlap_and_out_of_order_responses(self):
        for offset, count in ((75, 50), (0, 25), (125, 23), (25, 50), (25, 50)):
            self.pages.add(self.request, page(offset, count))
        result = self.pages.result()
        self.assertEqual(len(result["tracks"]), 148)
        self.assertEqual(result["tracks"][-1]["title"], "Song 147")
        self.assertEqual(result["tracks"][0]["duration"], 180)

    def test_does_not_call_partial_list_complete(self):
        self.pages.add(self.request, page(0, 100))
        self.assertFalse(self.pages.complete)
        with self.assertRaisesRegex(ValueError, "100/148"):
            self.pages.result()

    def test_targets_gaps_in_88_of_148_read_then_recovers_order(self):
        self.pages.add(self.request, page(0, 25))
        self.pages.add(self.request, page(85, 63))
        self.assertEqual(len(self.pages.items), 88)
        self.assertEqual(self.pages.first_missing, 25)
        self.pages.add(self.request, page(25, 50))
        self.assertEqual(self.pages.first_missing, 75)
        self.pages.add(self.request, page(75, 50))
        self.assertTrue(self.pages.complete)
        self.assertEqual(self.pages.result()["tracks"][147]["title"], "Song 147")

    def test_ignores_other_playlists_and_recommendations(self):
        self.pages.add({**self.request, "variables": {"uri": "spotify:playlist:other"}}, page(0, 25))
        self.pages.add({**self.request, "operationName": "recommendations"}, page(0, 25))
        self.assertEqual(self.pages.items, {})

    def test_rejects_playlist_changes_and_oversize(self):
        self.pages.add(self.request, page(0, 25))
        with self.assertRaisesRegex(ValueError, "change"):
            self.pages.add(self.request, page(25, 50, total=149))
        with self.assertRaisesRegex(ValueError, "500"):
            self.pages.add(self.request, page(25, 50, total=501))

    def test_unavailable_entries_are_not_silently_dropped(self):
        payload = page(0, 1, total=1)
        payload["data"]["playlistV2"]["content"]["items"][0]["itemV2"] = {"data": {"__typename": "NotFound"}}
        self.pages.add(self.request, payload)
        self.assertIn("indisponible", self.pages.result()["tracks"][0]["title"])
