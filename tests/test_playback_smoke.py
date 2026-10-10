from contextlib import nullcontext
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import test_playback as smoke


class PlaybackSmokeTests(unittest.TestCase):
    def app(self, rows):
        app = Mock()
        db = Mock()
        db.execute.return_value.fetchall.return_value = rows
        app.local_db.side_effect = lambda: nullcontext(db)
        app.delivery_for_job.return_value = {'mime_type': 'audio/flac'}
        app.create_stream_ticket.return_value = '/api/stream/job?ticket=private-test-token'
        return app, db

    def test_missing_audio_is_not_reported_as_success(self):
        app, _ = self.app([])
        self.assertEqual(smoke.test_provider(app, 'tiddl', 'http://localhost', 2)[0], 'ABSENT')
        app.create_stream_ticket.assert_not_called()

    def test_http_failure_hides_and_revokes_ticket(self):
        app, db = self.app([{'job_id': 'job', 'user_id': 'owner', 'title': 'Song'}])
        with patch.object(smoke, 'urlopen', side_effect=HTTPError('private-test-token', 403, 'denied', {}, None)):
            status, detail = smoke.test_provider(app, 'tiddl', 'http://localhost', 2)
        self.assertEqual(status, 'ECHEC')
        self.assertIn('403', detail)
        self.assertNotIn('private-test-token', detail)
        self.assertIn('delete from stream_tickets', db.execute.call_args.args[0])

    def test_success_decodes_audio_and_revokes_ticket(self):
        app, db = self.app([{'job_id': 'job', 'user_id': 'owner', 'title': 'Song'}])
        response = Mock(status=206, headers={'Content-Range': 'bytes 0-65535/99999', 'Content-Type': 'audio/flac'})
        response.read.return_value = b'f'
        with patch.object(smoke, 'urlopen', return_value=nullcontext(response)), \
             patch.object(smoke.subprocess, 'run', return_value=Mock(returncode=0)) as decode:
            status, _ = smoke.test_provider(app, 'tiddl', 'http://localhost', 2)
        self.assertEqual(status, 'OK')
        self.assertEqual(decode.call_args.args[0][0], 'ffmpeg')
        self.assertIn('delete from stream_tickets', db.execute.call_args.args[0])
