import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import test_webapp as fixtures
from test_webapp import webapp


class AdminActivityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WebAppHTTPTests.asyncSetUp
    asyncTearDown = fixtures.WebAppHTTPTests.asyncTearDown

    async def test_recent_successful_downloads_sort_accounts_and_split_daily_totals(self):
        users = [webapp.local_create_user(f'activity{i}@example.com', 'secret123')['id'] for i in range(3)]
        today = datetime.now(ZoneInfo('Europe/Paris')).date()
        yesterday = today - timedelta(days=1)
        with webapp.local_db() as db:
            for i, (owner, state, finished, count) in enumerate([
                (users[0], 'done', f'{today}T10:00:00Z', 2),
                (users[1], 'done', f'{today}T11:00:00Z', 3),
                (users[0], 'done', f'{today}T09:00:00Z', 1),
                (users[2], 'failed', f'{today}T12:00:00Z', 9),
            ]):
                db.execute('insert into download_history(job_id,user_id,provider,source_url,state,created_at,completed_at,track_count) values(?,?,?,?,?,?,?,?)',
                           (f'job-{i}', owner, 'tiddl', 'https://tidal.com/track/1', state, f'{yesterday}T12:00:00Z', finished, count))
        profiles = webapp.local_admin_profiles()
        self.assertEqual([p['user_id'] for p in profiles], [users[1], users[0], users[2]])
        self.assertEqual(profiles[-1]['last_download_at'], '')
        days = webapp.normalize_admin_daily_stats(webapp.local_daily_history_rows(), days=2)
        self.assertEqual(days[0]['tracks'], 0)
        self.assertEqual(days[0]['users'], [])
        self.assertEqual(days[1]['tracks'], 6)
        self.assertEqual(sorted(u['tracks'] for u in days[1]['users']), [3, 3])
        self.assertEqual(sum(u['tracks'] for u in days[1]['users']), days[1]['tracks'])

    def test_day_boundary_uses_paris_completion_time(self):
        today = datetime.now(ZoneInfo('Europe/Paris')).date()
        yesterday = today - timedelta(days=1)
        days = webapp.normalize_admin_daily_stats([{
            'created_at': f'{yesterday}T12:00:00Z', 'completed_at': f'{yesterday}T23:30:00Z',
            'user_id': 'one', 'email': 'one@example.com', 'state': 'done', 'track_count': 2,
        }], days=2)
        self.assertEqual(days[0]['tracks'], 0)
        self.assertEqual(days[1]['tracks'], 2)
