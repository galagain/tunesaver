import json
import unittest
from datetime import datetime, timezone
from unittest import mock

import test_webapp as fixtures
from test_webapp import musicdl, webapp
import audio_retention as retention


class AudioRetentionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WebAppHTTPTests.asyncSetUp
    asyncTearDown = fixtures.WebAppHTTPTests.asyncTearDown

    def completed(self, age=retention.RETENTION_SECONDS + 1):
        user = webapp.local_create_user('retention@example.com', 'secret123')
        identifier = musicdl.enqueue('tiddl', ['track/123'], owner_id=user['id'], ephemeral=True,
                                     metadata={'title': 'Kept title', 'artist': 'Artist', 'kind': 'track'})
        pending = musicdl.QUEUE_ROOT / 'pending' / 'tiddl' / f'{identifier}.json'
        done = musicdl.QUEUE_ROOT / 'done' / 'tiddl' / pending.name
        job = musicdl.load_job(pending, expected_provider='tiddl')
        self.now = 1900000000
        job['last_finished_at'] = datetime.fromtimestamp(self.now - age, timezone.utc).isoformat()
        directory = musicdl.DELIVERY_ROOT / user['id'] / identifier
        directory.mkdir(parents=True)
        audio = directory / 'song.flac'
        audio.write_bytes(b'audio')
        job['delivery'] = {'name': audio.name, 'path': str(audio.relative_to(musicdl.DELIVERY_ROOT)), 'size': 5}
        musicdl.atomic_write_json(done, job)
        pending.unlink()
        return user['id'], identifier, done, directory

    async def test_expiry_preserves_library_source_and_playlist_then_requeues_same_id(self):
        owner, identifier, path, directory = self.completed()
        webapp.local_upsert_history(owner, [retention.history(json.loads(path.read_text()))])
        playlist = webapp.local_create_playlist(owner, 'Keep playlist')
        webapp.local_playlist_add_track(owner, playlist['id'], identifier)
        self.assertEqual(retention.expire(webapp, self.now), 1)
        self.assertFalse(directory.exists())
        self.assertTrue(path.exists())
        track = webapp.local_library_tracks(owner)[0]
        self.assertTrue(track['expired'])
        self.assertFalse(track['streamable'])
        self.assertEqual(track['title'], 'Kept title')
        self.assertEqual(track['source_url'], 'https://tidal.com/track/123')
        self.assertIn(identifier, webapp.local_playlists(owner)[0]['track_ids'])
        result = retention.requeue(webapp, owner, identifier)
        self.assertFalse(result['duplicate'])
        self.assertEqual(result['job_id'], identifier)
        self.assertTrue(retention.requeue(webapp, owner, identifier)['duplicate'])
        self.assertIn(identifier, webapp.local_playlists(owner)[0]['track_ids'])
        job = musicdl.load_job(musicdl.QUEUE_ROOT / 'pending' / 'tiddl' / path.name, expected_provider='tiddl')
        self.assertNotIn('last_finished_at', job)
        self.assertNotIn('delivery_expired_at', job)
        self.assertEqual(job['urls'], ['track/123'])

    async def test_exactly_seven_days_keeps_audio(self):
        _, _, _, directory = self.completed(retention.RETENTION_SECONDS)
        self.assertEqual(retention.expire(webapp, self.now), 0)
        self.assertTrue(directory.exists())

    async def test_unknown_completion_date_keeps_audio(self):
        _, _, path, directory = self.completed()
        job = json.loads(path.read_text())
        job['last_finished_at'] = 'invalid'
        musicdl.atomic_write_json(path, job)
        self.assertEqual(retention.expire(webapp, self.now), 0)
        self.assertTrue(directory.exists())

    async def test_metadata_failure_prevents_deletion(self):
        _, _, path, directory = self.completed()
        with mock.patch.object(webapp, 'local_upsert_history', side_effect=RuntimeError('DB unavailable')):
            with self.assertRaises(RuntimeError):
                retention.expire(webapp, self.now)
        self.assertTrue(directory.exists())
        self.assertIn('delivery', json.loads(path.read_text()))

    async def test_symlink_is_not_followed(self):
        _, _, _, directory = self.completed()
        target = directory.with_name('unrelated')
        directory.rename(target)
        directory.symlink_to(target, target_is_directory=True)
        self.assertEqual(retention.expire(webapp, self.now), 0)
        self.assertTrue((target / 'song.flac').exists())

    async def test_pending_cleanup_retries_after_failure(self):
        _, _, path, directory = self.completed()
        with mock.patch.object(retention.shutil, 'rmtree', side_effect=OSError('busy')):
            self.assertEqual(retention.expire(webapp, self.now), 0)
        self.assertTrue(json.loads(path.read_text())['retention_pending'])
        self.assertEqual(retention.expire(webapp, self.now), 1)
        self.assertFalse(directory.exists())
        self.assertEqual(retention.expire(webapp, self.now), 0)

    async def test_requeue_checks_owner_and_worker(self):
        owner, identifier, _, _ = self.completed()
        retention.expire(webapp, self.now)
        with self.assertRaises(webapp.PublicError) as error:
            retention.requeue(webapp, fixtures.TEST_USER_ID, identifier)
        self.assertEqual(error.exception.status, 404)
        with mock.patch.object(webapp, 'worker_is_active', return_value=False):
            with self.assertRaises(webapp.PublicError) as error:
                retention.requeue(webapp, owner, identifier)
        self.assertEqual(error.exception.status, 503)
