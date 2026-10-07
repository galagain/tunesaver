import json
import unittest
from unittest import mock

import test_webapp as fixtures
from test_webapp import webapp


class PlaylistAppendTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WebAppHTTPTests.asyncSetUp
    asyncTearDown = fixtures.WebAppHTTPTests.asyncTearDown

    async def test_removed_track_returns_at_end_and_duplicate_keeps_position(self):
        owner = webapp.local_create_user('append@example.com', 'secret123')['id']
        playlist = webapp.local_create_playlist(owner, 'Set')['id']
        jobs = [f'20260909T100000Z-{i:08x}' for i in range(4)]
        with webapp.local_db() as db:
            for i, job in enumerate(jobs):
                db.execute('insert into download_history(job_id,user_id,provider,source_url,state,created_at) values(?,?,?,?,?,?)',
                           (job, owner, 'tiddl', f'https://tidal.com/track/{i+1}', 'done', '2026-09-09'))
        with mock.patch.object(webapp, 'utc_timestamp', return_value='2026-10-08T00:00:00Z'):
            for job in jobs:
                webapp.local_playlist_add_track(owner, playlist, job)
            with webapp.local_db() as db:
                db.execute('insert into library_orders(user_id,scope,track_ids) values(?,?,?)',
                           (owner, f'playlist:{playlist}', json.dumps(jobs[:2][::-1])))
            webapp.local_playlist_remove_track(owner, playlist, jobs[1])
            webapp.local_playlist_add_track(owner, playlist, jobs[1])
            webapp.local_playlist_add_track(owner, playlist, jobs[0])
        self.assertEqual(webapp.local_playlists(owner)[0]['track_ids'], [jobs[0], jobs[2], jobs[3], jobs[1]])
        self.assertEqual(webapp.local_collection_export(owner, playlist)['links'],
                         [f'https://tidal.com/track/{i}' for i in [1, 3, 4, 2]])
