import json
import unittest
import test_webapp as fixtures
from test_webapp import webapp, musicdl


class AdminFailuresTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WebAppHTTPTests.asyncSetUp
    asyncTearDown = fixtures.WebAppHTTPTests.asyncTearDown

    async def test_unsynced_failed_jobs_and_imports_are_visible_only_for_owner(self):
        owner = webapp.local_create_user('failure@example.com', 'secret123')['id']
        other = webapp.local_create_user('other@example.com', 'secret123')['id']
        job = musicdl.enqueue('qobuz', ['https://play.qobuz.com/track/123'], owner_id=owner,
                              metadata={'title': 'Failed song', 'artist': 'Artist'})
        path = musicdl.QUEUE_ROOT / 'pending' / 'qobuz' / (job + '.json')
        payload = musicdl.load_job(path)
        payload['last_error'] = 'Access denied'
        musicdl.atomic_write_json(path, payload)
        path.rename(musicdl.QUEUE_ROOT / 'failed' / 'qobuz' / path.name)
        playlist = webapp.local_create_playlist(owner, 'Import')
        with webapp.local_db() as db:
            db.execute('insert into playlist_imports(id,user_id,playlist_id,source_url,created_at,tracks,failures) values(?,?,?,?,?,?,?)',
                       ('import-test', owner, playlist['id'], 'https://tidal.com/playlist/test', '2026-10-10',
                        json.dumps([{'title': 'Missing', 'artist': 'Artist', 'source_url': 'https://tidal.com/track/456'}]),
                        json.dumps([{'title': 'Missing', 'artist': 'Artist', 'reason': 'No match'}])))
        rows = webapp.local_admin_history(owner)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row['error'] for row in rows}, {'Access denied', 'No match'})
        self.assertTrue(all(row['state'] == 'failed' for row in rows))
        self.assertEqual(webapp.local_admin_history(other), [])
        self.assertEqual(webapp.local_user_history(owner), [])
        self.assertEqual(len(webapp.local_admin_history(owner)), 2)
        webapp.local_upsert_history(owner, [{'job_id': job, 'provider': 'qobuz', 'state': 'failed'}])
        self.assertEqual(len(webapp.local_admin_history(owner)), 2)
