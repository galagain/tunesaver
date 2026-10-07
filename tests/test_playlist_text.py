import json
import unittest
from unittest import mock

import test_webapp as fixtures
from test_webapp import webapp, musicdl
import test_audio_retention
import import_engine as engine
import audio_retention


class PlaylistTextTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WebAppHTTPTests.asyncSetUp
    asyncTearDown = fixtures.WebAppHTTPTests.asyncTearDown
    completed = test_audio_retention.AudioRetentionTests.completed

    async def test_export_import_preserves_metadata_order_and_reuses_existing_file_offline(self):
        owner, identifier, path, directory = self.completed(age=0)
        webapp.local_upsert_history(owner, [audio_retention.history(json.loads(path.read_text()))])
        playlist = webapp.local_create_playlist(owner, 'Mon set')
        webapp.local_playlist_add_track(owner, playlist['id'], identifier)
        with mock.patch.object(webapp.mixer_analysis, 'summaries', return_value={identifier: {'duration': 185}}):
            exported = webapp.local_collection_export(owner, playlist['id'], detailed=True)
        document = json.loads(exported['text'])
        self.assertEqual(document['name'], 'Mon set')
        self.assertEqual(document['tracks'][0], {'title': 'Kept title', 'artist': 'Artist', 'duration_seconds': 185, 'url': 'https://tidal.com/track/123'})
        request = fixtures.FakeRequest({'document': document, 'auto_download': True}, app=self.app_state)
        request['user'] = {'id': owner}
        result = fixtures.response_json(await webapp.playlist_import_handler(request))
        def record():
            with webapp.local_db() as db:
                return dict(db.execute('select * from playlist_imports where id=?', (result['id'],)).fetchone())
        await webapp.process_playlist_import(self.app_state, record())
        self.assertEqual(record()['state'], 'running')
        with mock.patch.object(webapp, 'worker_is_active', return_value=False), \
             mock.patch.object(engine.matcher.Catalog, 'search_tracks', side_effect=AssertionError('Native link')):
            await webapp.process_playlist_import(self.app_state, record())
        self.assertEqual(record()['state'], 'done')
        imported = next(p for p in webapp.local_playlists(owner) if p['id'] == result['playlist_id'])
        self.assertEqual(imported['name'], 'Mon set')
        self.assertEqual(imported['track_ids'], [identifier])
        self.assertEqual(list((musicdl.QUEUE_ROOT / 'pending' / 'tiddl').glob('*.json')), [])
        self.assertTrue(directory.exists())

    async def test_normal_album_auto_download_moves_from_pending_to_running(self):
        request = fixtures.FakeRequest({'url': 'https://tidal.com/album/1', 'auto_download': True}, app=self.app_state)
        request['user'] = webapp.local_create_user('automatic@example.com', 'secret123')
        result = fixtures.response_json(await webapp.playlist_import_handler(request))
        with webapp.local_db() as db:
            record = dict(db.execute('select * from playlist_imports where id=?', (result['id'],)).fetchone())
        with mock.patch.object(webapp, 'read_import_source', return_value={'name': 'Real album', 'kind': 'album', 'tracks': [{'title': 'Song', 'artist': 'Artist'}]}):
            await webapp.process_playlist_import(self.app_state, record)
        with webapp.local_db() as db:
            self.assertEqual(db.execute('select state from playlist_imports where id=?', (result['id'],)).fetchone()[0], 'running')

    async def test_completed_reuse_is_private_and_requires_file(self):
        owner, identifier, path, directory = self.completed(age=0)
        url = 'https://listen.tidal.com/browse/track/123?share=1'
        self.assertEqual(webapp.existing_job('tiddl', url, owner, completed=True), identifier)
        self.assertIsNone(webapp.existing_job('tiddl', url, 'other-user', completed=True))
        with mock.patch.object(webapp, 'delivery_for_job', return_value=None):
            self.assertIsNone(webapp.existing_job('tiddl', url, owner, completed=True))

    def test_resource_identity_ignores_slug_locale_and_share_query(self):
        pairs = [
            ('tiddl', 'track/123', 'https://tidal.com/browse/track/123?share=1'),
            ('beatport', 'https://www.beatport.com/track/song/123', 'https://www.beatport.com/fr/track/renamed/123'),
            ('qobuz', 'https://www.qobuz.com/fr-fr/track/song/123', 'https://open.qobuz.com/track/123'),
        ]
        for provider, left, right in pairs:
            with self.subTest(provider=provider):
                self.assertIsNotNone(webapp.resource_identity(provider, left))
                self.assertEqual(webapp.resource_identity(provider, left), webapp.resource_identity(provider, right))

    def test_document_validation_rejects_unsafe_links_and_bad_metadata(self):
        valid = {'format': 'tunesaver-playlist', 'version': 1, 'name': 'Set', 'tracks': [
            {'title': 'Song', 'artist': 'Artist', 'duration_seconds': None, 'url': 'https://tidal.com/track/1'}]}
        self.assertEqual(engine.playlist_document(valid), valid)
        for changes in [{'url': 'https://localhost/track/1'}, {'url': 'https://tidal.com/album/1'},
                        {'duration_seconds': float('nan')}, {'title': {}}, {'duration_seconds': -1}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                engine.playlist_document({**valid, 'tracks': [{**valid['tracks'][0], **changes}]})
