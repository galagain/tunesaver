import threading
import unittest
from unittest import mock
import test_webapp as fixtures
from test_webapp import webapp


class StreamResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_ticket_disk_and_database_work_does_not_block_event_loop(self):
        main_thread = threading.get_ident()
        def create(job, owner):
            self.assertNotEqual(threading.get_ident(), main_thread)
            return '/api/stream/test?ticket=private'
        with mock.patch.object(webapp, 'create_stream_ticket', side_effect=create):
            response = await webapp.stream_ticket_handler(fixtures.FakeRequest({'job_id': 'test'}))
        self.assertEqual(response.status, 200)

    async def test_stream_validation_does_not_block_event_loop(self):
        main_thread = threading.get_ident()
        def resolve(job, ticket):
            self.assertNotEqual(threading.get_ident(), main_thread)
            raise webapp.PublicError('Invalid', status=403)
        with mock.patch.object(webapp, 'resolve_stream', side_effect=resolve):
            with self.assertRaises(webapp.PublicError):
                await webapp.stream_handler(fixtures.FakeRequest({}, match_info={'job_id': 'test'}))
