import base64
import hashlib
import struct
import time
import unittest
import zlib

from aiohttp.test_utils import TestClient, TestServer
import test_webapp as fixtures
from test_webapp import webapp


def tiny_png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND', b'')


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.WebAppHTTPTests.asyncSetUp(self)
        self.users = [webapp.local_create_user(f'feedback{i}@example.com', 'secret123') for i in range(2)]
        self.app = webapp.create_app()
        self.app.cleanup_ctx.clear()
        for token, user in zip(('owner', 'other'), self.users):
            self.app[webapp.AUTH_CACHE_KEY][hashlib.sha256(token.encode()).hexdigest()] = (time.monotonic() + 3600, user)
        self.app[webapp.ADMIN_SESSIONS_KEY]['administrator'] = time.monotonic() + 3600
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await fixtures.WebAppHTTPTests.asyncTearDown(self)

    async def request(self, method, path, token='owner', data=None):
        return await self.client.request(method, path, headers={'Authorization': f'Bearer {token}'}, json=data)

    async def create(self, **extra):
        response = await self.request('POST', '/api/feedback', data={'kind': 'bug', 'title': 'Lecture', 'body': 'Le son ne part pas.', **extra})
        self.assertEqual(response.status, 201, await response.text())
        return (await response.json())['id']

    async def test_private_thread_replies_status_unread_and_stale_acknowledgement(self):
        identifier = await self.create(context={'browser': 'Firefox/140', 'password': 'never-store'})
        path = f'/api/feedback/{identifier}'
        admin_path = f'/api/admin/feedback/{identifier}'
        self.assertEqual((await self.client.get(path)).status, 401)
        self.assertEqual((await self.request('GET', path, 'other')).status, 404)
        self.assertEqual((await self.request('POST', path + '/comment', 'other', {'body': 'no'})).status, 404)
        self.assertEqual((await self.request('GET', admin_path)).status, 401)
        self.assertEqual((await (await self.request('GET', '/api/feedback', 'other')).json())['threads'], [])
        data = await (await self.request('GET', path)).json()
        self.assertNotIn('password', data['context'])
        await self.request('POST', admin_path + '/comment', 'administrator', {'body': 'Corrigé.'})
        listing = await (await self.request('GET', '/api/feedback')).json()
        self.assertTrue(listing['threads'][0]['unread'])
        await self.request('POST', path + '/read', data={'revision': 1})
        self.assertTrue((await (await self.request('GET', '/api/feedback')).json())['threads'][0]['unread'])
        await self.request('POST', path + '/read', data={'revision': 2})
        self.assertFalse((await (await self.request('GET', '/api/feedback')).json())['threads'][0]['unread'])
        await self.request('POST', admin_path + '/status', 'administrator', {'status': 'resolved'})
        data = await (await self.request('GET', path)).json()
        self.assertEqual(data['status'], 'resolved')
        self.assertEqual([m['author'] for m in data['messages']], ['user', 'admin', 'status'])
        response = await self.request('POST', admin_path + '/status', 'administrator', {'status': 'invalid'})
        self.assertEqual(response.status, 400)

    async def test_capture_protected_and_cascades_on_account_deletion(self):
        image = tiny_png()
        identifier = await self.create(screenshot=base64.b64encode(image).decode())
        path = f'/api/feedback/{identifier}/screenshot'
        self.assertEqual((await self.client.get(path)).status, 401)
        self.assertEqual((await self.request('GET', path, 'other')).status, 404)
        response = await self.request('GET', path)
        self.assertEqual(await response.read(), image)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        response = await self.request('GET', f'/api/admin/feedback/{identifier}/screenshot', 'administrator')
        self.assertEqual(response.status, 200)
        webapp.local_delete_user(self.users[0]['id'])
        with webapp.local_db() as db:
            self.assertEqual(db.execute('select count(*) from feedback_messages').fetchone()[0], 0)

    async def test_validation_and_daily_limit(self):
        for invalid in [{'kind': 'unknown'}, {'title': ''}, {'body': 'a' * 8001},
                        {'screenshot': base64.b64encode(b'<svg onload="alert(1)"/>').decode()}, {'context': []}]:
            response = await self.request('POST', '/api/feedback', data={'kind': 'bug', 'title': 'Test', 'body': 'Test', **invalid})
            self.assertEqual(response.status, 400, await response.text())
        for _ in range(10):
            await self.create()
        response = await self.request('POST', '/api/feedback', data={'kind': 'bug', 'title': 'Test', 'body': 'Test'})
        self.assertEqual(response.status, 429)
