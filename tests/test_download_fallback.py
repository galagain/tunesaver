import json
import unittest
from unittest import mock

import test_webapp as fixtures
from test_webapp import webapp, musicdl
import download_fallback as fallback


class DownloadFallbackTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WebAppHTTPTests.asyncSetUp
    asyncTearDown = fixtures.WebAppHTTPTests.asyncTearDown

    def failed(self):
        user = webapp.local_create_user('fallback@example.com', 'secret123')
        with webapp.local_db() as db:
            db.execute('update users set approved=1 where id=?', (user['id'],))
        identifier = musicdl.enqueue('beatport', ['https://www.beatport.com/track/song/123'],
                                     owner_id=user['id'], ephemeral=True,
                                     metadata={'title': 'Song', 'artist': 'Artist', 'kind': 'track'})
        pending = musicdl.QUEUE_ROOT / 'pending' / 'beatport' / f'{identifier}.json'
        path = musicdl.QUEUE_ROOT / 'failed' / 'beatport' / pending.name
        job = musicdl.load_job(pending)
        job.update(attempts=1, last_finished_at=musicdl.utc_now())
        musicdl.atomic_write_json(path, job)
        pending.unlink()
        log = musicdl.QUEUE_ROOT / 'logs' / 'beatport' / f'{identifier}-attempt-1.log'
        log.write_text('Requested quality is not available')
        webapp.local_upsert_history(user['id'], [{'job_id': identifier, 'provider': 'beatport',
            'source_url': job['urls'][0], 'title': 'Song', 'artist': 'Artist',
            'resource_kind': 'track', 'state': 'failed', 'created_at': job['created_at']}])
        return user['id'], identifier, path

    def catalog(self):
        reference = {'title': 'Song', 'artist': 'Artist', 'duration': 180}
        catalog = mock.Mock()
        catalog.track.return_value = reference
        catalog.search_tracks.side_effect = lambda p, q: [{**reference, 'provider': p,
            'source_url': 'https://tidal.com/track/456', 'download_url': 'https://tidal.com/track/456'}] if p == 'tidal' else []
        return catalog

    async def test_replacement_keeps_id_playlist_and_skips_failed_provider(self):
        owner, identifier, path = self.failed()
        playlist = webapp.local_create_playlist(owner, 'Set')
        webapp.local_playlist_add_track(owner, playlist['id'], identifier)
        catalog = self.catalog()
        with mock.patch.object(fallback, 'Catalog', return_value=catalog), mock.patch.object(webapp, 'worker_is_active', return_value=True):
            self.assertTrue(fallback.retry(webapp, path))
        self.assertFalse(path.exists())
        result = musicdl.load_job(musicdl.QUEUE_ROOT / 'pending' / 'tiddl' / path.name)
        self.assertEqual(result['id'], identifier)
        self.assertEqual(result['fallback_tried'], ['beatport'])
        self.assertEqual(result['owner_id'], owner)
        self.assertEqual(result['urls'], ['https://tidal.com/track/456'])
        self.assertIn(identifier, webapp.local_playlists(owner)[0]['track_ids'])
        self.assertEqual({c.args[0] for c in catalog.search_tracks.call_args_list}, {'tidal'})
        self.assertEqual(webapp.local_user_history(owner)[0]['provider'], 'tiddl')

    async def test_no_match_is_checked_once_without_downgrading(self):
        _, _, path = self.failed()
        catalog = self.catalog()
        catalog.search_tracks.side_effect = None
        catalog.search_tracks.return_value = [{'title': 'Song', 'artist': 'Artist', 'mix': 'Other Remix', 'duration': 180}]
        with mock.patch.object(fallback, 'Catalog', return_value=catalog), mock.patch.object(webapp, 'worker_is_active', return_value=True):
            self.assertFalse(fallback.retry(webapp, path))
            count = catalog.search_tracks.call_count
            self.assertFalse(fallback.retry(webapp, path))
            self.assertEqual(catalog.search_tracks.call_count, count)
        self.assertIn('Qualite', json.loads(path.read_text())['last_error'])

    async def test_failed_replacement_does_not_return_to_previous_platform(self):
        _, _, path = self.failed()
        job = musicdl.load_job(path)
        job['fallback_tried'] = ['tiddl']
        job['fallback_reference'] = {'title': 'Song', 'artist': 'Artist', 'duration': 180}
        musicdl.atomic_write_json(path, job)
        catalog = self.catalog()
        catalog.search_tracks.side_effect = lambda p, q: [{'title': 'Song', 'artist': 'Artist', 'duration': 180,
            'provider': 'qobuz', 'download_url': 'https://play.qobuz.com/track/789'}]
        with mock.patch.object(fallback, 'Catalog', return_value=catalog), mock.patch.object(webapp, 'worker_is_active', return_value=True):
            self.assertTrue(fallback.retry(webapp, path))
        self.assertEqual({c.args[0] for c in catalog.search_tracks.call_args_list}, {'qobuz'})
        catalog.track.assert_not_called()
        result = musicdl.load_job(musicdl.QUEUE_ROOT / 'pending' / 'qobuz' / path.name)
        self.assertEqual(result['fallback_tried'], ['tiddl', 'beatport'])

    async def test_deleted_during_lookup_is_not_recreated(self):
        owner, identifier, path = self.failed()
        catalog = self.catalog()
        def lookup(*args):
            webapp.local_library_remove_track(owner, identifier)
            return {'title': 'Song', 'artist': 'Artist', 'duration': 180}
        catalog.track.side_effect = lookup
        with mock.patch.object(fallback, 'Catalog', return_value=catalog), mock.patch.object(webapp, 'worker_is_active', return_value=True):
            self.assertFalse(fallback.retry(webapp, path))
        self.assertFalse((musicdl.QUEUE_ROOT / 'pending' / 'tiddl' / path.name).exists())

    async def test_blocked_and_unrelated_failures_do_not_retry(self):
        owner, identifier, path = self.failed()
        with webapp.local_db() as db:
            db.execute('update users set blocked=1 where id=?', (owner,))
        with mock.patch.object(fallback, 'Catalog') as catalog:
            self.assertFalse(fallback.retry(webapp, path))
            catalog.assert_not_called()
        (musicdl.QUEUE_ROOT / 'logs' / 'beatport' / f'{identifier}-attempt-1.log').write_text('Disk full')
        self.assertEqual(fallback.reason(webapp, musicdl.load_job(path)), '')
