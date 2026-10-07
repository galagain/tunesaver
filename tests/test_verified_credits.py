import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catalog_matcher import Catalog, MatchError, match_track, match_collection


class VerifiedCreditsTests(unittest.TestCase):
    source = {'title': 'The Shit Baby', 'artist': 'Omar S, D Taylor', 'duration': 413.315}
    candidate = {'title': 'The Shit Baby', 'artist': 'Omar-S', 'duration': '6:53',
                 'source_url': 'https://listen.tidal.com/track/77157515'}

    def test_uses_explicit_featured_credit_without_relaxing_matching(self):
        catalog = Catalog()
        with mock.patch.object(catalog, 'api', return_value={'items': [
            {'name': 'D Taylor', 'role': 'Featured Artist'},
            {'name': 'FXHE MUSIC ASCAP', 'role': 'Music Publisher'},
        ]}) as api:
            self.assertIsNone(match_track(self.source, [self.candidate])[0])
            enriched = catalog.with_verified_credits('tidal', self.source, [self.candidate])
            self.assertEqual(match_track(self.source, enriched)[0]['artist'], 'Omar-S, D Taylor')
            api.assert_called_once_with('tidal', 'tracks/77157515/contributors', limit=100)
            self.assertEqual(self.candidate['artist'], 'Omar-S')

    def test_composers_unknown_guests_and_missing_credits_do_not_match(self):
        for credits in [[], [{'name': 'D Taylor', 'role': 'Composer'}],
                        [{'name': 'Someone Else', 'role': 'Featured Artist'}]]:
            catalog = Catalog()
            with mock.patch.object(catalog, 'api', return_value={'items': credits}):
                enriched = catalog.with_verified_credits('tidal', self.source, [self.candidate])
                self.assertIsNone(match_track(self.source, enriched)[0])
        with mock.patch.object(catalog, 'api', side_effect=MatchError('Unavailable')):
            self.assertIsNone(match_track(self.source, catalog.with_verified_credits('tidal', self.source, [self.candidate]))[0])

    def test_only_close_recordings_trigger_credit_lookup(self):
        catalog = Catalog()
        with mock.patch.object(catalog, 'api', side_effect=AssertionError('Unexpected request')):
            for change in [{'title': 'Other song'}, {'mix': 'Club Remix'}, {'artist': 'Someone Else'},
                           {'duration': 420}, {'duration': None}]:
                candidate = {**self.candidate, **change}
                self.assertEqual(catalog.with_verified_credits('tidal', self.source, [candidate]), [candidate])
            self.assertEqual(catalog.with_verified_credits('beatport', self.source, [self.candidate]), [self.candidate])

    def test_collection_resolves_to_tidal_before_other_catalogs(self):
        catalog = Catalog()
        with mock.patch.object(catalog, 'search_tracks', return_value=[self.candidate]) as search, \
             mock.patch.object(catalog, 'api', return_value={'items': [{'name': 'D Taylor', 'role': 'Featured Artist'}]}):
            result = match_collection({'name': 'Test', 'kind': 'track', 'tracks': [self.source]}, catalog)
        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['tracks'][0]['match']['provider'], 'tidal')
        self.assertEqual(search.call_count, 1)
