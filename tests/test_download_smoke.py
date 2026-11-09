import unittest
from unittest import mock
import test_webapp as fixtures
from test_webapp import webapp, download_engine
import test_downloads as smoke


class DownloadSmokeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.WebAppHTTPTests.asyncSetUp
    asyncTearDown = fixtures.WebAppHTTPTests.asyncTearDown

    async def test_completed_probe_decodes_and_cleans_only_its_own_job(self):
        other = download_engine.enqueue('qobuz', ['https://play.qobuz.com/track/42'])
        enqueue = download_engine.enqueue
        roots = []

        def complete(provider, urls, **kwargs):
            job = enqueue(provider, urls, **kwargs)
            path = download_engine.QUEUE_ROOT / 'pending' / provider / (job + '.json')
            payload = download_engine.load_job(path, expected_provider=provider)
            content = download_engine.DELIVERY_ROOT / kwargs['owner_id'] / job / 'content'
            content.mkdir(parents=True)
            (content / 'test.flac').write_bytes(b'fixture-audio')
            roots.append(content.parent)
            download_engine.prepare_delivery(payload, content)
            download_engine.atomic_write_json(path, payload)
            path.rename(download_engine.QUEUE_ROOT / 'done' / provider / path.name)
            return job

        with mock.patch.object(download_engine, 'enqueue', side_effect=complete), \
             mock.patch.object(smoke.subprocess, 'run', return_value=mock.Mock(returncode=0)) as decoder:
            ok, _ = smoke.run_test(webapp, 'qobuz', 'https://play.qobuz.com/track/123', 10)
        self.assertTrue(ok)
        decoder.assert_called_once()
        self.assertFalse(roots[0].exists())
        self.assertTrue((download_engine.QUEUE_ROOT / 'pending' / 'qobuz' / (other + '.json')).exists())
        self.assertEqual(list((download_engine.QUEUE_ROOT / 'done' / 'qobuz').glob('*.json')), [])

    async def test_stopped_worker_does_not_enqueue(self):
        with mock.patch.object(webapp, 'worker_is_active', return_value=False), \
             mock.patch.object(download_engine, 'enqueue') as enqueue:
            ok, message = smoke.run_test(webapp, 'tiddl', 'https://tidal.com/track/1', 10)
        self.assertFalse(ok)
        self.assertIn('ARRETE', message)
        enqueue.assert_not_called()

    async def test_album_is_not_accepted_as_one_track_probe(self):
        with self.assertRaises(ValueError):
            smoke.choose_url(webapp, 'tiddl', 'https://tidal.com/album/1')
