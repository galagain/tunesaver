import contextlib
import http.client
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from test_matching_runner import payload
import matching_web as web
import run_matching_tests as runner


class MatchingWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        source = Path(self.temp.name) / 'inputs.json'
        source.write_text(json.dumps({'schema_version': 1, 'collections': [payload('tiddl', 'track')]}))
        self.controller = web.Controller(source, Path(self.temp.name) / 'output')

    def test_events_update_tracks_without_double_counting(self):
        controller = self.controller
        controller.scenarios['tiddl-track']['selected'] = True
        with mock.patch.object(runner, 'EVENTS', True), contextlib.redirect_stderr(io.StringIO()) as stream:
            runner.match_payload(payload('tiddl', 'track'), mock.Mock())
        for line in stream.getvalue().splitlines():
            if line.startswith('TUNESAVER_EVENT:'):
                controller.event(json.loads(line.split(':', 1)[1]))
        self.assertEqual(controller.snapshot()['done'], 1)
        self.assertEqual(controller.snapshot()['matched'], 1)

    def test_command_is_fixed_and_duplicate_start_refused(self):
        with mock.patch.object(web.subprocess, 'Popen') as launch, mock.patch.object(web.threading.Thread, 'start'):
            self.controller.start(['tiddl-track'])
            args = launch.call_args.args[0]
            self.assertIn('--docker', args)
            self.assertIn('--events', args)
            self.assertEqual(args[-2:], ['--case', 'tiddl-track'])
            with self.assertRaises(RuntimeError):
                self.controller.start(['tiddl-track'])
        with self.assertRaises(ValueError):
            self.controller.start(['; rm anything'])

    def test_search_events_show_errors_and_provider_fallback(self):
        import catalog_matcher
        catalog = mock.Mock()
        def search(provider, query):
            if provider == 'tidal':
                raise RuntimeError('private-token')
            return [{'title': 'Song', 'artist': 'Artist', 'duration': 180,
                     'source_url': 'https://www.beatport.com/track/song/1'}]
        catalog.search_tracks.side_effect = search
        events = []
        result = catalog_matcher.match_collection({'kind': 'playlist', 'name': 'Test',
            'tracks': [{'title': 'Song', 'artist': 'Artist', 'duration': 180}]}, catalog, on_event=events.append)
        self.assertEqual(result['matched'], 1)
        self.assertEqual([e['provider'] for e in events if e['type'] == 'provider'], ['tidal', 'beatport'])
        self.assertTrue(any(e.get('error') for e in events))
        self.assertNotIn('private-token', json.dumps(events))
        self.assertTrue(events[-1]['final'])
        self.assertEqual(events[-1]['target']['provider'], 'beatport')

    def test_interrupted_worker_is_removed_using_cidfile(self):
        cid = 'b' * 64
        def run(command, **kwargs):
            if command[:2] == ['docker', 'run']:
                Path(command[command.index('--cidfile') + 1]).write_text(cid)
                raise KeyboardInterrupt
            return mock.Mock(returncode=0)
        with mock.patch.object(runner.subprocess, 'run', side_effect=run) as process:
            with self.assertRaises(KeyboardInterrupt):
                runner.docker_match(payload('tiddl', 'track'), 'image', 'container')
        self.assertEqual(process.call_args.args[0], ['docker', 'rm', '--force', cid])

    def test_stop_removes_only_job_containers(self):
        self.controller.status = 'stopping'
        cid = 'a' * 64
        with mock.patch.object(web.os, 'killpg'), mock.patch.object(web.subprocess, 'run', return_value=mock.Mock(stdout=cid)) as run:
            self.controller.cancel(mock.Mock(pid=123), 'job-id')
        self.assertIn('label=tunesaver.matching-job=job-id', run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0], ['docker', 'rm', '-f', cid])
        self.assertEqual(self.controller.status, 'cancelled')

    def test_failed_cleanup_blocks_restart(self):
        with mock.patch.object(web.os, 'killpg'), mock.patch.object(web.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'docker')):
            self.controller.cancel(mock.Mock(pid=123), 'job-id')
        self.assertEqual(self.controller.status, 'cleanup_error')
        with self.assertRaises(RuntimeError):
            self.controller.start(['tiddl-track'])

    def test_abrupt_exit_marks_unfinished_scenario(self):
        self.controller.status = 'running'
        self.controller.scenarios['tiddl-track']['status'] = 'running'
        self.controller.consume(mock.Mock(stdout=io.StringIO(''), wait=mock.Mock(return_value=2)))
        self.assertEqual(self.controller.status, 'error')
        self.assertEqual(self.controller.scenarios['tiddl-track']['status'], 'error')

    def test_http_protection_and_routes(self):
        server = web.make_server('127.0.0.1', 0, self.controller)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection(*server.server_address)
        self.addCleanup(connection.close)
        def request(path, headers=None):
            connection.request('GET', path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        self.assertEqual(request('/api/state')[0], 403)
        headers = {'X-Matching-Token': server.token}
        code, body = request('/api/state', headers)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['status'], 'idle')
        self.assertEqual(request('/api/state', {**headers, 'Origin': 'https://evil.test'})[0], 403)
        self.assertEqual(request('/', {'Host': 'evil.test'})[0], 403)
        self.assertEqual(request('/.env')[0], 404)
        self.assertEqual(request('/api/artifact?name=../../.env', headers)[0], 404)
        self.assertIn(server.token.encode(), request('/')[1])


if __name__ == '__main__':
    unittest.main()
