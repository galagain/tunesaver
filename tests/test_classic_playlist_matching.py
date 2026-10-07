import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catalog_matcher import match_track


class ClassicPlaylistMatchingTests(unittest.TestCase):
    def test_five_reported_versions(self):
        cases = [
            ('He Not In - Original', 'Chicken Lips', 521.84,
             'He Not In', 'Chicken Lips', '', '8:41'),
            ('Come Into My Room - Dub', 'Sandy Rivera, LT Brown, Soul Vision', 465.861,
             'Come Into My Room feat. LT Brown', 'Sandy Rivera', 'Dub Mix', '7:45'),
            ("At Night - Peggy Gou's Acid Journey Remix", 'Shakedown, Peggy Gou', 431.281,
             'At Night', 'Shakedown', "Peggy Gou's Acid Journey Remix", '7:11'),
            ('The Age Of Love - Jam & Spoon Watch Out For Stella Mix', 'Age Of Love, Jam & Spoon', 408.773,
             'The Age Of Love', 'Age Of Love', 'Jam & Spoon Watch Out For Stella Mix', '6:49'),
            ('Spanish Fly - Original', '2000 And One', 455.469,
             'Spanish Fly', '2000 And One', 'Original', '7:35'),
        ]
        for title, artist, duration, target_title, target_artist, mix, length in cases:
            source = dict(title=title, artist=artist, duration=duration)
            target = dict(title=target_title, artist=target_artist, duration=length, mix=mix)
            with self.subTest(title=title):
                self.assertIs(match_track(source, [target])[0], target)
                self.assertIsNone(match_track(source, [{**target, 'mix': 'Unrelated Remix'}])[0])
                self.assertIsNone(match_track(source, [{**target, 'duration': 100}])[0])

    def test_original_is_a_label_not_any_occurrence_in_title(self):
        self.assertIsNone(match_track({'title': 'The Original', 'artist': 'Artist'},
                                     [{'title': 'The', 'artist': 'Artist'}])[0])
        source = dict(title='He Not In - Original', artist='Chicken Lips', duration=521.84)
        self.assertIsNone(match_track(source, [dict(title='He Not In', artist='Chicken Lips', duration='8:31')])[0])

    def test_omitted_contributors_require_version_lead_duration_and_subset(self):
        source = dict(title='Song - Dub', artist='Main Artist, Other Contributor', duration=300)
        target = dict(title='Song', mix='Dub Mix', artist='Main Artist', duration=300)
        self.assertIs(match_track(source, [target])[0], target)
        for change in ({'duration': None}, {'duration': 303}, {'artist': 'Other Contributor'},
                       {'artist': 'Main Artist, Unknown'}, {'mix': 'Club Mix'}, {'mix': ''}):
            with self.subTest(change=change):
                self.assertIsNone(match_track(source, [{**target, **change}])[0])
        self.assertIsNone(match_track({**source, 'title': 'Song'}, [{**target, 'mix': ''}])[0])
        self.assertIsNone(match_track(target, [source])[0])

    def test_remixer_mentions_in_main_title_are_not_credits(self):
        source = dict(title='A Song About Peggy Gou', artist='Shakedown, Peggy Gou', duration=300)
        self.assertIsNone(match_track(source, [{**source, 'artist': 'Shakedown'}])[0])
