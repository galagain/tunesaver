import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from aiohttp.test_utils import TestClient, TestServer
import test_webapp as fixtures
from test_webapp import webapp
import catalog_sync as catalog

ROW = {'title':'Track', 'artist':'Artist', 'url':'https://tidal.com/track/123','provider':'tiddl'}

class CatalogHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.WebAppHTTPTests.asyncSetUp(self)
        self.app=webapp.create_app()
        self.client=TestClient(TestServer(self.app));await self.client.start_server()
        self.config=catalog.issue(webapp,label='test')
        self.headers={'Authorization':'Bearer '+self.config['token']}
        self.app[webapp.ADMIN_SESSIONS_KEY]['admin-test']=webapp.time.monotonic()+3600
        self.admin={'Authorization':'Bearer admin-test'}
    async def asyncTearDown(self):
        await self.client.close()
        await fixtures.WebAppHTTPTests.asyncTearDown(self)
    async def test_idempotent_across_devices_and_admin_only(self):
        for config in (self.config,catalog.issue(webapp)):
            r=await self.client.post('/api/catalog-sync',headers={'Authorization':'Bearer '+config['token']},json={'tracks':[ROW,{**ROW,'title':'Alternative tag'}]})
            self.assertEqual(r.status,200,await r.text())
        r=await self.client.get('/api/admin/catalog',headers=self.admin)
        self.assertEqual((await r.json())['total'],1)
        r=await self.client.get('/api/admin/catalog/export',headers=self.admin)
        self.assertEqual(await r.json(),[ROW])
        self.assertEqual((await self.client.get('/api/admin/catalog',headers=self.headers)).status,401)
        self.assertEqual((await self.client.post('/api/catalog-sync',json={'tracks':[ROW]})).status,401)
    async def test_no_audio_paths_or_arbitrary_urls(self):
        for row in ({**ROW,'audio':'bytes'},{**ROW,'path':'/private/music'}, {**ROW,'url':'file:///private'}, {**ROW,'url':'https://evil.example/track'}):
            r=await self.client.post('/api/catalog-sync',headers=self.headers,json={'tracks':[row]})
            self.assertEqual(r.status,400)
        self.assertEqual((await (await self.client.get('/api/admin/catalog',headers=self.admin)).json())['total'],0)
    async def test_revocation_and_user_approval(self):
        with webapp.local_db() as db:
            db.execute('update catalog_devices set revoked=1')
        self.assertEqual((await self.client.post('/api/catalog-sync',headers=self.headers,json={'tracks':[ROW]})).status,401)
        user=webapp.local_create_user('sync@example.com','test-password')
        config=catalog.issue(webapp,user['id'])
        headers={'Authorization':'Bearer '+config['token']}
        self.assertEqual((await self.client.post('/api/catalog-sync',headers=headers,json={'tracks':[ROW]})).status,403)

class OutboxTests(unittest.TestCase):
    def test_offline_retry_and_idempotent_ack(self):
        with tempfile.TemporaryDirectory() as data:
            catalog.enqueue(data,[ROW,ROW])
            with mock.patch('requests.post',side_effect=OSError('offline')):
                with self.assertRaises(OSError):catalog.flush(data,{'server':catalog.SERVER,'token':'x'*40})
            with catalog.local_db(data) as db:self.assertEqual(db.execute('select count(*) from outbox where sent=0').fetchone()[0],1)
            with mock.patch('requests.post',return_value=mock.Mock(status_code=200)) as send:
                result=catalog.flush(data,{'server':catalog.SERVER,'token':'x'*40})
                self.assertEqual(send.call_args.kwargs['json'],{'tracks':[ROW]})
                self.assertEqual(result['pending'],0)
                catalog.flush(data,{'server':catalog.SERVER,'token':'x'*40});self.assertEqual(send.call_count,1)
            self.assertEqual((Path(data)/'catalog-sync.sqlite').stat().st_mode & 0o777,0o600)
    def test_album_identity_ignores_database_fields(self):
        row = {**ROW, "url":"https://tidal.com/album/123"}
        self.assertEqual(catalog.fingerprint(row), catalog.fingerprint({**row,"id":"old","added_at":"date"}))

    def test_reject_other_destination(self):
        with self.assertRaises(ValueError):catalog.valid_config({'server':'https://evil.example','token':'x'*40})

if __name__=='__main__':unittest.main()
