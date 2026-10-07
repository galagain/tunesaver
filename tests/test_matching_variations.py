import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import playlist_sources as sources
import catalog_matcher as matcher


def song(title='Song', artist='Main Artist', **extra):
    return {'title': title, 'artist': artist, 'duration': 300, **extra}


class MatchingVariationsTests(unittest.TestCase):
    def test_equivalent_metadata_in_both_directions(self):
        cases = [
            (song("Don't Stop"), song('Dont Stop')),
            (song('L\u2019amour'), song("L'amour")),
            (song('Reve', 'Christian Loffler'), song('Rêve', 'Christian Löffler')),
            (song('Song', 'Alice & Bob'), song('Song', 'Bob, Alice')),
            (song('Song', 'Alice x Bob'), song('Song', 'Alice; Bob')),
            (song('Song', 'Alice and Bob'), song('Song', 'Alice, Bob')),
            (song('Song', 'Alice vs. Bob'), song('Song', 'Alice, Bob')),
            (song('Song (feat. Jane Doe)'), song('Song', 'Main Artist, janedoe')),
            (song('Song (feat. janedoe)'), song('Song (feat. Jane Doe)')),
            (song('Song (feat. Jane Doe & John Smith)'), song('Song', 'Main Artist, janedoe, johnsmith')),
            (song('Song', 'Main Artist feat. Jane Doe & John Smith'), song('Song', 'Main Artist, Jane Doe, John Smith')),
            (song('Song - Liem Remix', 'Liem, Main Artist'), song('Song', mix='Liem Remix')),
            (song('Song - Liem Remix', 'Main Artist & Liem'), song('Song', mix='Liem Remix')),
            (song('Song (Extended Version)'), song('Song', mix='Extended Mix')),
            (song('Song - Radio Version'), song('Song', mix='Radio Edit', duration=301)),
            (song('夜空', '星'), song('夜空', '星')),
            (song('Музыка', 'Артист'), song('Музыка', 'Артист')),
        ]
        for left, right in cases:
            for wanted, candidate in ((left, right), (right, left)):
                with self.subTest(wanted=wanted, candidate=candidate):
                    self.assertIs(matcher.match_track(wanted, [candidate])[0], candidate)

    def test_rejects_dangerous_near_matches(self):
        cases = [
            (song('Song - Liem Remix', 'Liem, Main Artist'), song('Song', mix='Other Remix')),
            (song('Song - Liem Remix', 'Liem, Main Artist'), song('Song')),
            (song('Song - Liem Remix', 'Liem, Main Artist'), song('Song', mix='Liem Remix', duration=270)),
            (song('Song (feat. Jane Doe)'), song('Song (feat. Other Guest)')),
            (song('Song', 'Alice x Bob'), song('Song', 'Alice x Charlie')),
            (song('Song'), song('Song', 'Cover Artist')),
            (song('Song'), song('Song', mix='Live')),
            (song('Song'), song('Song', mix='Instrumental')),
            (song('Song'), song('Song', mix='Karaoke')),
            (song('Song'), song('Song', mix='Sped Up')),
            (song('Song'), song('Song', mix='Slowed')),
            (song('Song (Extended Mix)'), song('Song (Radio Edit)')),
            (song('Song (Extended Version)'), song('Song (Extended Mix)', duration=None)),
            (song('Song (Extended Version)'), song('Song (Extended Mix)', duration=305)),
            (song('Dublin'), song('Dublin', mix='Dub')),
            (song('夜空', '星'), song('天空', '星')),
            (song('Song', 'Артист'), song('Song', 'Другой')),
        ]
        for wanted, candidate in cases:
            with self.subTest(wanted=wanted, candidate=candidate):
                self.assertIsNone(matcher.match_track(wanted, [candidate])[0])

    def test_title_only_fallback_still_checks_artist_and_version(self):
        wanted = song('Song - Liem Remix', 'Liem, Main Artist')
        good = song('Song', mix='Liem Remix')
        catalog = mock.Mock()
        catalog.search_tracks.side_effect = lambda p, q: [song('Song', 'Other', mix='Liem Remix'), good] if q == 'song liem remix' else []
        result = matcher.match_collection({'kind': 'playlist', 'name': 'Test', 'tracks': [wanted]}, catalog)
        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['tracks'][0]['match']['artist'], 'Main Artist')
        self.assertTrue(all(call.args[0] == 'tidal' for call in catalog.search_tracks.call_args_list))
        self.assertEqual(result['tracks'][0]['attempts'][0]['candidates_checked'], 2)

    def test_queries_are_deduplicated_and_keep_named_remix(self):
        wanted = song('Song - Liem Remix', 'Main Artist, Liem')
        queries = matcher.queries(wanted)
        self.assertEqual(len(queries), len({sources.normalized_words(q) for q in queries}))
        self.assertLessEqual(len(queries), 5)
        self.assertTrue(all('liem remix' in q.lower() for q in queries))
        self.assertIn('Main Artist song', matcher.queries(song('Song - Extended Mix')))

    def test_exact_match_does_not_run_fallback_searches(self):
        catalog = mock.Mock()
        catalog.search_tracks.return_value = [song()]
        result = matcher.match_collection({'kind': 'playlist', 'name': 'Test', 'tracks': [song()]}, catalog)
        self.assertEqual(result['matched'], 1)
        catalog.search_tracks.assert_called_once()
