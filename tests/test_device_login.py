import json
import time
import unittest
from unittest import mock
from aiohttp.test_utils import TestClient,TestServer
import test_webapp as fixtures
from test_webapp import webapp,download_engine

class DeviceLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.WebAppHTTPTests.asyncSetUp(self)
        self.client=TestClient(TestServer(webapp.create_app()));await self.client.start_server()
        user=webapp.local_create_user('device@example.com','test-password');self.user=user
        with webapp.local_db() as db:db.execute('update users set approved=1,email_verified=1 where id=?',(user['id'],))
        self.headers={'Authorization':'Bearer '+webapp.session_payload(user)['access_token']}
        (download_engine.SECRETS_ROOT/'beatport_username').write_text('fixture-user')
        (download_engine.SECRETS_ROOT/'beatport_password').write_text('fixture-secret')
    async def asyncTearDown(self):
        await self.client.close();await fixtures.WebAppHTTPTests.asyncTearDown(self)
    async def grant(self):return await (await self.client.post('/api/device/start',json={})).json()
    async def test_complete_grant_is_one_time(self):
        grant=await self.grant();body={'device_token':grant['device_token']}
        self.assertNotIn(grant['device_token'],grant['verification_uri'])
        self.assertEqual((await self.client.post('/api/device/poll',json=body)).status,202)
        self.assertEqual((await self.client.post('/api/device/approve',json={'code':grant['code'],'providers':['beatport']})).status,401)
        r=await self.client.post('/api/device/approve',headers=self.headers,json={'code':grant['code'],'providers':['beatport']});self.assertEqual(r.status,200)
        r=await self.client.post('/api/device/poll',json=body);self.assertEqual(r.status,200,await r.text())
        bundle=await r.json();self.assertEqual(bundle['accounts']['beatport']['beatport_username'],'fixture-user');self.assertIn('sync',bundle)
        self.assertEqual(r.headers['Cache-Control'],'no-store')
        self.assertEqual((await self.client.post('/api/device/poll',json=body)).status,410)
    async def test_revoked_user_cannot_collect(self):
        grant=await self.grant()
        await self.client.post('/api/device/approve',headers=self.headers,json={'code':grant['code'],'providers':['beatport']})
        with webapp.local_db() as db:db.execute('update users set approved=0 where id=?',(self.user['id'],))
        self.assertEqual((await self.client.post('/api/device/poll',json={'device_token':grant['device_token']})).status,403)
    async def test_expired_and_wrong_secret(self):
        grant=await self.grant()
        self.assertEqual((await self.client.post('/api/device/poll',json={'device_token':'x'*43})).status,410)
        with webapp.local_db() as db:db.execute('update device_logins set expires=?',(time.time()-1,))
        self.assertEqual((await self.client.post('/api/device/approve',headers=self.headers,json={'code':grant['code'],'providers':['beatport']})).status,410)
    async def test_cross_origin_approval_is_rejected(self):
        grant=await self.grant()
        r=await self.client.post('/api/device/approve',headers={**self.headers,'Origin':'https://evil.test'},json={'code':grant['code'],'providers':['beatport']})
        self.assertEqual(r.status,403)

if __name__=='__main__':unittest.main()
