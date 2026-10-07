import unittest
from unittest import mock

import test_webapp as fixtures
from test_webapp import webapp, musicdl
import import_engine as engine


class SingleTrackImportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WebAppHTTPTests.asyncSetUp
    asyncTearDown = fixtures.WebAppHTTPTests.asyncTearDown

    def test_resource_type_not_number_of_links_decides_destination(self):
        singles = [
            'https://tidal.com/track/123', 'https://www.beatport.com/fr/track/song/123',
            'https://play.qobuz.com/track/123', 'https://soundcloud.com/artist/song',
            'https://open.spotify.com/track/4m2880jivSbbyEGAKfITCa',
            'https://music.apple.com/us/album/album/123?i=456',
        ]
        collections = [
            'https://tidal.com/album/123', 'https://www.beatport.com/release/album/123',
            'https://play.qobuz.com/album/123', 'https://soundcloud.com/artist/sets/album',
            'https://open.spotify.com/playlist/57HAIqxdHbi332994xcOKc',
            'https://open.spotify.com/album/4m2880jivSbbyEGAKfITCa',
            'https://music.apple.com/us/album/album/123',
        ]
        for url in singles:
            with self.subTest(url=url): self.assertTrue(engine.is_single_track(url))
        for url in collections:
            with self.subTest(url=url): self.assertFalse(engine.is_single_track(url))

    async def test_native_track_queues_once_without_playlist_or_matching(self):
        user = webapp.local_create_user('single@example.com', 'secret123')
        request = fixtures.FakeRequest({'url': 'https://tidal.com/track/123'}, app=self.app_state)
        request['user'] = user
        source = {'name': 'Song', 'kind': 'track', 'source': 'https://tidal.com/track/123',
                  'tracks': [{'title': 'Song', 'artist': 'Artist', 'provider': 'tidal', 'source_url': 'https://tidal.com/track/123'}]}
        with mock.patch.object(engine, 'read', return_value=source), \
             mock.patch.object(engine.matcher.Catalog, 'search_tracks', side_effect=AssertionError('Native link needs no search')):
            first = fixtures.response_json(await webapp.playlist_import_handler(request))
            second = fixtures.response_json(await webapp.playlist_import_handler(request))
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertIsNone(first['playlist_id'])
        self.assertTrue((musicdl.QUEUE_ROOT / 'pending' / 'tiddl' / (first['job_id'] + '.json')).exists())
        with webapp.local_db() as db:
            self.assertEqual(db.execute('select count(*) from playlists').fetchone()[0], 0)
            self.assertEqual(db.execute('select count(*) from playlist_imports').fetchone()[0], 0)

    async def test_public_track_matches_and_does_not_create_playlist_on_failure(self):
        user = webapp.local_create_user('public@example.com', 'secret123')
        source = {'name': 'Song', 'kind': 'track', 'source': 'https://soundcloud.com/artist/song',
                  'tracks': [{'title': 'Song', 'artist': 'Artist'}]}
        candidate = {'title': 'Song', 'artist': 'Artist', 'provider': 'qobuz', 'source_url': 'https://play.qobuz.com/track/123'}
        with mock.patch.object(engine, 'read', return_value=source), \
             mock.patch.object(engine.matcher.Catalog, 'search_tracks', side_effect=lambda provider, query: [candidate] if provider == 'qobuz' else []):
            result = engine.add_single_track(webapp, user['id'], source['source'])
        self.assertEqual(result['kind'], 'track')
        with mock.patch.object(engine, 'read', return_value=source), \
             mock.patch.object(engine.matcher.Catalog, 'search_tracks', return_value=[]):
            with self.assertRaises(webapp.PublicError) as error:
                engine.add_single_track(webapp, user['id'], source['source'])
        self.assertEqual(error.exception.status, 422)
        self.assertEqual(webapp.local_playlists(user['id']), [])

    async def test_album_link_still_creates_collection_without_synchronous_read(self):
        request = fixtures.FakeRequest({'url': 'https://tidal.com/album/123'}, app=self.app_state)
        request['user'] = webapp.local_create_user('album@example.com', 'secret123')
        with mock.patch.object(engine, 'read', side_effect=AssertionError('Album remains asynchronous')):
            result = fixtures.response_json(await webapp.playlist_import_handler(request))
        self.assertTrue(result['playlist_id'])
        self.assertNotIn('job_id', result)
