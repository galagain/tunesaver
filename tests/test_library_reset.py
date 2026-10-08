import unittest
import test_webapp as fixtures
from test_webapp import webapp, musicdl


class LibraryResetTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WebAppHTTPTests.asyncSetUp
    asyncTearDown = fixtures.WebAppHTTPTests.asyncTearDown

    async def test_reset_is_confirmed_and_scoped_to_current_account(self):
        owner = webapp.local_create_user('reset@example.com', 'secret123')['id']
        other = webapp.local_create_user('other@example.com', 'secret123')['id']
        jobs = []
        for user in (owner, other):
            playlist = webapp.local_create_playlist(user, 'Keep or reset')
            for state in ('pending', 'failed', 'done'):
                job = musicdl.enqueue('qobuz', ['https://play.qobuz.com/track/123'], owner_id=user, ephemeral=True)
                path = musicdl.QUEUE_ROOT / 'pending' / 'qobuz' / (job + '.json')
                path.rename(musicdl.QUEUE_ROOT / state / 'qobuz' / path.name)
                content = musicdl.DELIVERY_ROOT / user / job / 'content'
                content.mkdir(parents=True)
                (content / 'test.flac').write_bytes(b'audio')
                webapp.local_upsert_history(user, [{'job_id': job, 'provider': 'qobuz', 'state': state}])
                jobs.append((user, job, content))
            with webapp.local_db() as db:
                db.execute('insert into playlist_imports(id,user_id,playlist_id,source_url,created_at) values(?,?,?,?,?)',
                           (user, user, playlist['id'], 'https://tidal.com/playlist/test', '2026-10-08'))
                db.execute('insert into library_orders(user_id,scope,track_ids) values(?,?,?)', (user, 'all', '[]'))
        request = fixtures.FakeRequest({}, app=self.app_state)
        request['user'] = {'id': owner}
        with self.assertRaises(webapp.PublicError):
            await webapp.library_reset_handler(request)
        self.assertEqual(len(webapp.local_user_history(owner)), 3)
        request = fixtures.FakeRequest({'confirm': 'RESET', 'user_id': other}, app=self.app_state)
        request['user'] = {'id': owner}
        await webapp.library_reset_handler(request)
        self.assertEqual(webapp.local_user_history(owner), [])
        self.assertEqual(webapp.local_playlists(owner), [])
        self.assertEqual(webapp.local_imports(owner), [])
        self.assertEqual(len(webapp.local_user_history(other)), 3)
        self.assertEqual(len(webapp.local_playlists(other)), 1)
        for user, job, content in jobs:
            self.assertEqual(content.exists(), user == other)
            if user == owner:
                webapp.local_upsert_history(owner, [{'job_id': job, 'provider': 'qobuz', 'state': 'done'}])
        self.assertEqual(webapp.local_user_history(owner), [])
        with webapp.local_db() as db:
            self.assertEqual(db.execute('select count(*) from users where id in (?,?)', (owner, other)).fetchone()[0], 2)
            self.assertEqual(db.execute('select count(*) from library_orders where user_id=?', (owner,)).fetchone()[0], 0)
        await webapp.library_reset_handler(request)
