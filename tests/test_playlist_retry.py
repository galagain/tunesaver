import json
import errno
import unittest
from unittest import mock

import test_audio_retention as retention_fixtures
import test_webapp as fixtures
from test_webapp import webapp, musicdl
import audio_retention
import import_engine


class PlaylistRetryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WebAppHTTPTests.asyncSetUp
    asyncTearDown = fixtures.WebAppHTTPTests.asyncTearDown

    async def test_missing_audio_requeues_once_and_preserves_membership(self):
        owner, identifier, path, directory = retention_fixtures.AudioRetentionTests.completed(self)
        job = musicdl.load_job(path)
        job.update(fallback_tried=['beatport', 'tiddl'], fallback_checked_at='old')
        musicdl.atomic_write_json(path, job)
        webapp.local_upsert_history(owner, [audio_retention.history(job)])
        playlist = webapp.local_create_playlist(owner, 'Set')['id']
        webapp.local_playlist_add_track(owner, playlist, identifier)
        self.assertEqual(webapp.retry_playlist(owner, playlist)['unchanged'], 1)
        (directory / 'song.flac').unlink()
        self.assertEqual(webapp.retry_playlist(owner, playlist, errors_only=True)['queued'], 0)
        replace = audio_retention.os.replace
        def separate_mounts(source, destination):
            if source == path:
                raise OSError(errno.EXDEV, 'separate Docker mounts')
            return replace(source, destination)
        with mock.patch.object(audio_retention.os, 'replace', side_effect=separate_mounts):
            result = webapp.retry_playlist(owner, playlist)
        self.assertEqual(result, {'queued': 1, 'unchanged': 0, 'matching': 0, 'errors': []})
        self.assertEqual(webapp.retry_playlist(owner, playlist)['unchanged'], 1)
        pending = musicdl.load_job(musicdl.QUEUE_ROOT / 'pending' / 'tiddl' / path.name)
        self.assertNotIn('fallback_tried', pending)
        self.assertNotIn('fallback_checked_at', pending)
        self.assertEqual(webapp.local_playlists(owner)[0]['track_ids'], [identifier])
        other = webapp.local_create_user('other-retry@example.com', 'secret123')['id']
        with self.assertRaises(webapp.PublicError):
            webapp.retry_playlist(other, playlist)

    async def test_retry_only_unmatched_import_rows(self):
        owner = webapp.local_create_user('retry-import@example.com', 'secret123')['id']
        playlist = webapp.local_create_playlist(owner, 'Set')['id']
        tracks = [{'title': 'Found', '_import': {'kind': 'playlist'},
                   'matching': {'status': 'matched', 'final': True, 'job_id': 'existing'}},
                  {'title': 'Missing', 'matching': {'status': 'not_found', 'final': True}}]
        with webapp.local_db() as db:
            db.execute('insert into playlist_imports(id,user_id,playlist_id,source_url,created_at,state,tracks,failures) '
                       'values(?,?,?,?,?,?,?,?)', ('retry-import', owner, playlist, 'https://tidal.com/playlist/test',
                                                webapp.utc_timestamp(), 'partial', json.dumps(tracks), '[{"title":"Missing"}]'))
        self.assertEqual(webapp.retry_playlist(owner, playlist)['matching'], 1)
        self.assertEqual(webapp.retry_playlist(owner, playlist)['matching'], 0)
        with webapp.local_db() as db:
            row = db.execute('select * from playlist_imports where id=?', ('retry-import',)).fetchone()
        updated = json.loads(row['tracks'])
        self.assertEqual(updated[0]['matching']['job_id'], 'existing')
        self.assertNotIn('matching', updated[1])
        self.assertEqual(row['state'], 'running')
        self.assertEqual(json.loads(row['failures']), [])
        def match_remaining(source, catalog, on_event):
            self.assertEqual([t['title'] for t in source['tracks']], ['Missing'])
            on_event({'type': 'track_updated', 'number': 1, 'status': 'not_found', 'final': True})
        with mock.patch.object(import_engine.matcher, 'match_collection', side_effect=match_remaining):
            import_engine.process(webapp, dict(row))
        with webapp.local_db() as db:
            row = db.execute('select tracks from playlist_imports where id=?', ('retry-import',)).fetchone()
        self.assertEqual(json.loads(row['tracks'])[0]['matching']['job_id'], 'existing')
