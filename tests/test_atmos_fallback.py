import unittest
from unittest import mock
import test_webapp as fixtures
from test_webapp import webapp, musicdl
from catalog_matcher import Catalog, match_collection, direct_target, match_album


class AtmosFallbackTests(unittest.TestCase):
    atmos = {'title': 'How It Feels', 'artist': 'Barry Can’t Swim', 'duration': '2:19',
             'provider': 'tiddl', 'source_url': 'https://tidal.com/track/297577870',
             'download_url': 'https://tidal.com/track/297577870', 'audio_modes': ['DOLBY_ATMOS', 'STEREO']}
    replacement = {**atmos, 'provider': 'beatport', 'source_url': 'https://www.beatport.com/track/song/1',
                   'download_url': 'https://www.beatport.com/track/song/1', 'audio_modes': []}

    def test_native_atmos_uses_next_catalog_and_not_direct(self):
        self.assertIsNone(direct_target(self.atmos))
        catalog = Catalog()
        with mock.patch.object(catalog, 'search_tracks', return_value=[self.replacement]) as search:
            result = match_collection({'name': 'Test', 'kind': 'track', 'tracks': [self.atmos]}, catalog)
        self.assertEqual(result['tracks'][0]['match']['provider'], 'beatport')
        self.assertEqual(search.call_args.args[0], 'beatport')

    def test_spotify_search_skips_atmos_and_falls_back_to_qobuz(self):
        def search(provider, query):
            return [self.atmos] if provider == 'tidal' else [] if provider == 'beatport' else [{**self.replacement, 'provider': 'qobuz'}]
        with mock.patch.object(Catalog, 'search_tracks', side_effect=search):
            result = match_collection({'name': 'Test', 'kind': 'track', 'tracks': [
                {'title': self.atmos['title'], 'artist': self.atmos['artist'], 'duration': 139}]}, Catalog())
        self.assertEqual(result['tracks'][0]['match']['provider'], 'qobuz')
        self.assertIsNone(match_album({'name': 'Test', 'tracks': [self.atmos]}, {'name': 'Test', 'tracks': [self.atmos]}))

    def test_no_wrong_version_when_no_replacement(self):
        with mock.patch.object(Catalog, 'track', return_value=self.atmos), \
             mock.patch.object(Catalog, 'search_tracks', return_value=[{**self.replacement, 'mix': 'Club Remix'}]):
            with self.assertRaisesRegex(webapp.PublicError, 'Aucune version équivalente'):
                webapp.compatible_download('tiddl', self.atmos['source_url'], {})

    def test_normalizer_keeps_atmos_marker(self):
        raw = {'id': 1, 'title': 'Test', 'allowStreaming': True, 'streamReady': True,
               'audioModes': ['STEREO'], 'mediaMetadata': {'tags': ['DOLBY_ATMOS']}}
        track = webapp.normalize_tidal_results({'tracks': {'items': [raw]}})[0]
        self.assertIn('DOLBY_ATMOS', track['audio_modes'])


class AtmosDownloadTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WebAppHTTPTests.asyncSetUp
    asyncTearDown = fixtures.WebAppHTTPTests.asyncTearDown

    async def test_saved_atmos_import_uses_replacement(self):
        user = webapp.local_create_user('atmos@example.com', 'secret123')
        with mock.patch.object(Catalog, 'track', return_value=AtmosFallbackTests.atmos), \
             mock.patch.object(Catalog, 'search_tracks', return_value=[AtmosFallbackTests.replacement]):
            job = webapp.enqueue_import_track({'user_id': user['id']}, AtmosFallbackTests.atmos)
        self.assertTrue((musicdl.QUEUE_ROOT / 'pending' / 'beatport' / (job + '.json')).exists())
        self.assertEqual(list((musicdl.QUEUE_ROOT / 'pending' / 'tiddl').glob('*.json')), [])

    async def test_download_enqueues_replacement_not_atmos(self):
        request = fixtures.FakeRequest({'provider': 'tiddl', 'url': AtmosFallbackTests.atmos['source_url']},
                                       path='/api/download', app=self.app_state)
        with mock.patch.object(Catalog, 'track', return_value=AtmosFallbackTests.atmos), \
             mock.patch.object(Catalog, 'search_tracks', return_value=[AtmosFallbackTests.replacement]):
            response = await webapp.api_middleware(request, webapp.download_handler)
        self.assertEqual(response.status, 202, response.text)
        self.assertEqual(len(list((musicdl.QUEUE_ROOT / 'pending' / 'beatport').glob('*.json'))), 1)
        self.assertEqual(list((musicdl.QUEUE_ROOT / 'pending' / 'tiddl').glob('*.json')), [])
