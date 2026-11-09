import json
import os
import unittest
from unittest import mock
from aiohttp.test_utils import TestClient, TestServer
import test_webapp as fixtures
from test_webapp import webapp, download_engine
from test_desktop import DesktopHTTPTests
import account_transfer

BUNDLE = {'format': account_transfer.FORMAT, 'accounts': {'beatport': {'beatport_username': 'sample', 'beatport_password': 'sample-pass'}}}

class ExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.WebAppHTTPTests.asyncSetUp(self)
        self.client = TestClient(TestServer(webapp.create_app()))
        await self.client.start_server()
        self.user = webapp.local_create_user('export@example.com', 'test-password')
        with webapp.local_db() as db:
            db.execute('update users set approved=1,email_verified=1 where id=?', (self.user['id'],))
        self.token = webapp.session_payload(self.user)['access_token']
        self.headers = {'Authorization': 'Bearer ' + self.token}
        for name, value in BUNDLE['accounts']['beatport'].items():
            (download_engine.SECRETS_ROOT / name).write_text(value)

    async def asyncTearDown(self):
        await self.client.close()
        await fixtures.WebAppHTTPTests.asyncTearDown(self)

    async def test_public_page_and_protected_export(self):
        self.assertEqual((await self.client.get('/auth/')).status, 200)
        self.assertEqual((await self.client.post('/api/account-export', json={'providers':['beatport']})).status, 401)
        response = await self.client.post('/api/account-export', headers=self.headers, json={'providers':['beatport']})
        self.assertEqual(response.status, 200, await response.text())
        payload = await response.json()
        self.assertEqual({k:payload[k] for k in BUNDLE}, BUNDLE)
        self.assertEqual(payload["sync"]["server"], "https://tunesaver.galagain.com")
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        # Cached normal login cannot bypass immediate revocation.
        with webapp.local_db() as db:
            db.execute('update users set approved=0 where id=?', (self.user['id'],))
        response = await self.client.post('/api/account-export', headers=self.headers, json={'providers':['beatport']})
        self.assertEqual(response.status, 403)

    async def test_session_reuse_does_not_create_or_delete_sessions(self):
        response = await self.client.get('/api/account-transfer/session', headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual((await response.json())['email'], 'export@example.com')
        response = await self.client.post('/api/account-export', headers=self.headers, json={'providers':['beatport']})
        self.assertEqual(response.status, 200)
        self.assertIsNotNone(webapp.local_user_for_access_token(self.token))

    async def test_qobuz_bundle_strips_server_paths(self):
        bundle = {'format': account_transfer.FORMAT, 'accounts': {'qobuz': {'config.ini':
            '[DEFAULT]\nuser_id=123\nuser_auth_token=fixture\ndownloads=/server/music\n'}}}
        value = account_transfer.validate(bundle)['qobuz']['config.ini']
        self.assertNotIn('/server/music', value)
        self.assertIn('user_auth_token = fixture', value)
    
    async def test_export_login_short_session_and_unapproved_refusal(self):
        response = await self.client.post('/api/account-transfer/login', json={'email':'export@example.com','password':'test-password'})
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertNotIn('refresh_token', data)
        with webapp.local_db() as db:
            row = db.execute('select expires_at from sessions where token_hash=?', (webapp.token_hash(data['access_token']),)).fetchone()
            import time
            self.assertLessEqual(row['expires_at'] - time.time(), 300)
            db.execute('update users set approved=0 where id=?', (self.user['id'],))
        response = await self.client.post('/api/account-transfer/login', json={'email':'export@example.com','password':'test-password'})
        self.assertEqual(response.status, 403)

    async def test_cross_origin_blocked(self):
        response = await self.client.post('/api/account-export', headers={**self.headers,'Origin':'https://evil.test'}, json={'providers':['beatport']})
        self.assertEqual(response.status, 403)

class ImportTests(DesktopHTTPTests):
    async def test_server_transfer_routes_are_disabled_locally(self):
        for path in ('/auth/', '/api/account-transfer/session'):
            self.assertEqual((await self.client.get(path, headers=self.cookie)).status, 404)
        for path in ('/api/account-transfer/login', '/api/account-export'):
            self.assertEqual((await self.client.post(path, headers=self.cookie, json={})).status, 404)

    async def test_bundle_import_persists_and_preserves_existing(self):
        response = await self.client.post('/api/desktop/account-import', headers=self.cookie, json=BUNDLE)
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual((await response.json())['imported'], ['beatport'])
        self.assertEqual(self.desktop.vault.get('beatport'), BUNDLE['accounts']['beatport'])
        self.assertEqual((download_engine.SECRETS_ROOT/'beatport_password').stat().st_mode & 0o777, 0o600)
        changed = {'format':account_transfer.FORMAT, 'accounts':{'beatport':{'beatport_username':'other','beatport_password':'other'}}}
        result = await self.client.post('/api/desktop/account-import', headers=self.cookie, json=changed)
        self.assertEqual((await result.json())['skipped'], ['beatport'])
        self.assertEqual(self.desktop.vault.get('beatport'), BUNDLE['accounts']['beatport'])

    async def test_invalid_file_no_write(self):
        for accounts in ({'../../escape':{}}, {'beatport':{'../../escape':'secret'}}, {'tiddl':{'auth.json':'[]'}}):
            response = await self.client.post('/api/desktop/account-import', headers=self.cookie, json={'format':account_transfer.FORMAT,'accounts':accounts})
            self.assertEqual(response.status, 400)
        self.assertEqual(self.desktop.vault.values, {})

    async def test_keychain_failure_rolls_back(self):
        with mock.patch.object(self.desktop.vault, 'set', side_effect=RuntimeError('locked')):
            response = await self.client.post('/api/desktop/account-import', headers=self.cookie, json=BUNDLE)
        self.assertEqual(response.status, 409)
        self.assertFalse((download_engine.SECRETS_ROOT/'beatport_password').exists())

if __name__ == '__main__': unittest.main()
