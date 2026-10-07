import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catalog_matcher import Catalog, MatchError, match_track, match_collection, qobuz_performing_artists


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
            self.assertEqual(catalog.with_verified_credits('unknown', self.source, [self.candidate]), [self.candidate])

    def test_qobuz_credit_roles_preserve_names_and_exclude_composers(self):
        self.assertEqual(qobuz_performing_artists({'performers':
            'D Taylor, FeaturedArtist - Earth, Wind & Fire, MainArtist, Producer - Unknown, Composer - Label, MusicPublisher'}),
            ['D Taylor', 'Earth, Wind & Fire'])
        self.assertEqual(qobuz_performing_artists({'artists': [
            {'name': 'D Taylor', 'roles': ['featured-artist']}, {'name': 'Writer', 'roles': ['composer']}]}), ['D Taylor'])

    def test_qobuz_and_beatport_verified_performers(self):
        for provider, detail in [
            ('qobuz', {'id': 1, 'title': 'The Shit Baby', 'performer': {'name': 'Omar S'}, 'duration': 413,
                       'performers': 'D Taylor, FeaturedArtist - Omar S, MainArtist - Someone, Composer'}),
            ('beatport', {'id': 1, 'name': 'The Shit Baby', 'mix_name': 'Original Mix', 'length': '6:53',
                         'artists': [{'name': 'Omar S'}, {'name': 'D Taylor'}]}),
        ]:
            candidate = {**self.candidate, 'source_url': 'https://www.beatport.com/fr/track/the-shit-baby/1'
                         if provider == 'beatport' else 'https://play.qobuz.com/track/1'}
            catalog = Catalog()
            with self.subTest(provider=provider), mock.patch.object(catalog, 'api', return_value=detail) as api:
                enriched = catalog.with_verified_credits(provider, self.source, [candidate])
                self.assertIsNotNone(match_track(self.source, enriched)[0])
                self.assertEqual(api.call_count, 1)
                if provider == 'qobuz':
                    api.assert_called_once_with(provider, 'track/get', track_id='1')
                else:
                    api.assert_called_once_with(provider, 'tracks/1/')
            for change in ({'duration': 500, 'length': '8:20'}, {'title': 'Wrong', 'name': 'Wrong'},
                           {'version': 'Club Remix', 'mix_name': 'Club Remix'}, {'id': 2}):
                with self.subTest(provider=provider, change=change), mock.patch.object(catalog, 'api', return_value={**detail, **change}):
                    enriched = catalog.with_verified_credits(provider, self.source, [candidate])
                    self.assertIsNone(match_track(self.source, enriched)[0])

    def test_beatport_does_not_invent_credits_and_can_verify_remixer(self):
        catalog = Catalog()
        candidate = {**self.candidate, 'source_url': 'https://www.beatport.com/track/song/1'}
        detail = {'id': 1, 'name': 'The Shit Baby', 'length': '6:53', 'artists': [{'name': 'Omar S'}]}
        with mock.patch.object(catalog, 'api', return_value=detail):
            self.assertIsNone(match_track(self.source, catalog.with_verified_credits('beatport', self.source, [candidate]))[0])
        detail = {**detail, 'mix_name': 'Remix', 'remixers': [{'name': 'D Taylor'}]}
        source = {**self.source, 'title': 'The Shit Baby - Remix'}
        with mock.patch.object(catalog, 'api', return_value=detail):
            enriched = catalog.with_verified_credits('beatport', source, [{**candidate, 'mix': 'Remix'}])
            self.assertIsNotNone(match_track(source, enriched)[0])

    def test_collection_resolves_to_tidal_before_other_catalogs(self):
        catalog = Catalog()
        with mock.patch.object(catalog, 'search_tracks', return_value=[self.candidate]) as search, \
             mock.patch.object(catalog, 'api', return_value={'items': [{'name': 'D Taylor', 'role': 'Featured Artist'}]}):
            result = match_collection({'name': 'Test', 'kind': 'track', 'tracks': [self.source]}, catalog)
        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['tracks'][0]['match']['provider'], 'tidal')
        self.assertEqual(search.call_count, 1)
