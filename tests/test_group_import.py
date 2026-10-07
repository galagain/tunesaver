import json
import unittest
from unittest import mock

import test_webapp as fixtures
from test_webapp import webapp
import import_engine as engine


class GroupImportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WebAppHTTPTests.asyncSetUp
    asyncTearDown = fixtures.WebAppHTTPTests.asyncTearDown

    def test_group_reads_every_source_and_deduplicates_exact_track_links(self):
        urls = ['https://tidal.com/track/1', 'https://tidal.com/album/2', 'https://tidal.com/track/1']
        one = {'title': 'One', 'artist': 'Artist', 'source_url': urls[0], 'provider': 'tidal'}
        two = {**one, 'title': 'Two', 'source_url': 'https://tidal.com/track/2'}
        with mock.patch.object(engine.matcher, 'read_input', side_effect=[
            {'name': 'One', 'kind': 'track', 'tracks': [one]},
            {'name': 'Album', 'kind': 'album', 'tracks': [one, two]},
        ]) as read:
            result = engine.read(json.dumps(urls))
        self.assertEqual(read.call_count, 2)
        self.assertEqual(result['tracks'], [one, two])
        self.assertEqual(result['kind'], 'playlist')
        self.assertFalse(engine.is_single_track(json.dumps([urls[0]])))

    async def test_group_creates_one_owned_import_and_keeps_custom_name(self):
        user = webapp.local_create_user('group@example.com', 'secret123')
        request = fixtures.FakeRequest({'urls': ['https://tidal.com/track/1', 'https://tidal.com/album/2'], 'name': 'Mon set'}, app=self.app_state)
        request['user'] = user
        result = fixtures.response_json(await webapp.playlist_import_handler(request))
        again = fixtures.response_json(await webapp.playlist_import_handler(request))
        self.assertEqual(result['id'], again['id'])
        with webapp.local_db() as db:
            record = dict(db.execute('select * from playlist_imports').fetchone())
        source = {'name': 'Playlist regroupée', 'kind': 'playlist', 'tracks': [{'title': 'One', 'artist': 'Artist'}]}
        with mock.patch.object(webapp, 'read_import_source', return_value=source):
            await webapp.process_playlist_import(self.app_state, record)
        playlists = webapp.local_playlists(user['id'])
        self.assertEqual(len(playlists), 1)
        self.assertEqual(playlists[0]['name'], 'Mon set')
        self.assertEqual(webapp.local_imports(user['id'])[0]['state'], 'ready')
        exported = engine.export_result({**record, 'tracks': json.dumps(source['tracks'])})
        self.assertEqual(exported['collections'][0]['source'], 'group')

    def test_bad_group_members_are_rejected_before_reading(self):
        for values in [[], ['https://127.0.0.1/track/1'], [{}], [['https://tidal.com/track/1']]]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                engine.identify(json.dumps(values))

    def test_group_failure_is_not_silently_omitted(self):
        with mock.patch.object(engine.matcher, 'read_input', side_effect=ValueError('Source unavailable')):
            with self.assertRaises(ValueError):
                engine.read(json.dumps(['https://tidal.com/album/1']))
