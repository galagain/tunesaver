import contextlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import import_engine as engine


class ImportEngineTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('create table playlist_imports (id text, state text, tracks text, processed int, queued int, failures text)')
        @contextlib.contextmanager
        def local_db():
            with self.db:
                yield self.db
        self.api = SimpleNamespace(local_db=local_db, PublicError=ValueError,
            worker_is_active=mock.Mock(return_value=True), enqueue_import_track=mock.Mock(return_value='job'))
        self.tracks = [{'title': 'Song', 'artist': 'Artist', 'duration': 180},
                       {'title': 'Second', 'artist': 'Artist', 'duration': 190}]
        self.candidate = {'title': 'Song', 'artist': 'Artist', 'duration': 180, 'source_url': 'https://tidal.com/track/1', 'provider': 'tidal'}

    def record(self, download=False, limit=1, kind='playlist'):
        self.tracks[0]['_import'] = {'name': 'Album', 'kind': kind, 'max_tracks': limit, 'download': download}
        self.db.execute("insert into playlist_imports values ('id','running',?,0,0,'[]')", (json.dumps(self.tracks),))
        return self.reload()

    def reload(self):
        row = dict(self.db.execute("select * from playlist_imports where id='id'").fetchone())
        return {**row, 'source_url': 'https://open.spotify.com/playlist/57HAIqxdHbi332994xcOKc', 'error': '', 'user_id': 'owner', 'playlist_id': 'playlist'}

    def test_search_only_limit_and_download_cached_matches(self):
        record = self.record()
        with mock.patch.object(engine.matcher.Catalog, 'search_tracks', return_value=[self.candidate]) as search:
            engine.process(self.api, record)
        self.assertEqual(search.call_count, 1)
        self.api.enqueue_import_track.assert_not_called()
        state = self.reload()
        self.assertEqual((state['state'], state['processed'], state['queued']), ('matched', 1, 0))
        tracks = json.loads(state['tracks'])
        self.assertEqual(tracks[0]['matching']['target']['provider'], 'tidal')
        self.assertNotIn('matching', tracks[1])
        exported = engine.export_result(state)['collections'][0]
        self.assertEqual((exported['total'], exported['source_total']), (1, 2))
        self.assertEqual(exported['tracks'][0]['target']['provider'], 'tidal')
        self.assertFalse(exported['tracks'][0]['requires_matching'])
        tracks[0]['_import']['download'] = True
        self.db.execute("update playlist_imports set state='running', tracks=?", (json.dumps(tracks),))
        with mock.patch.object(engine.matcher.Catalog, 'search_tracks', side_effect=AssertionError('Must reuse target')):
            engine.process(self.api, self.reload())
        self.assertEqual(self.reload()['queued'], 1)
        self.assertEqual(self.api.enqueue_import_track.call_args.args[1]['provider'], 'tiddl')
        self.db.execute("update playlist_imports set state='running'")
        engine.process(self.api, self.reload())
        self.api.enqueue_import_track.assert_called_once()

    def test_full_album_priority_with_limit(self):
        record = self.record(kind='album')
        with mock.patch.object(engine.matcher.Catalog, 'search_albums', return_value=[{'name': 'Album', 'url': 'https://tidal.com/album/1',
            'tracks': [self.candidate, {**self.candidate, 'title': 'Second', 'duration': 190}]}]), \
             mock.patch.object(engine.matcher.Catalog, 'search_tracks', side_effect=AssertionError('Album first')):
            engine.process(self.api, record)
        self.assertEqual(json.loads(self.reload()['tracks'])[0]['matching']['method'], 'album')

    def test_native_targets_skip_search(self):
        self.tracks[0].update(self.candidate)
        with mock.patch.object(engine.matcher.Catalog, 'search_tracks', side_effect=AssertionError('Direct target')):
            engine.process(self.api, self.record(download=True))
        self.assertEqual(self.reload()['queued'], 1)

    def test_stop_prevents_enqueue(self):
        record = self.record(download=True)
        def search(*args):
            self.db.execute("update playlist_imports set state='cancelled'")
            return [self.candidate]
        with mock.patch.object(engine.matcher.Catalog, 'search_tracks', side_effect=search):
            engine.process(self.api, record)
        self.api.enqueue_import_track.assert_not_called()
        self.assertEqual(self.reload()['state'], 'cancelled')

    def test_provider_priority_and_failures(self):
        record = self.record(download=True)
        calls = []
        def search(provider, query):
            calls.append(provider)
            return []
        with mock.patch.object(engine.matcher.Catalog, 'search_tracks', side_effect=search):
            engine.process(self.api, record)
        self.assertEqual(list(dict.fromkeys(calls)), ['tidal', 'beatport', 'qobuz'])
        self.assertEqual(self.reload()['state'], 'partial')
        self.assertEqual(len(json.loads(self.reload()['failures'])), 1)

    def test_supported_inputs_and_ssrf_validation(self):
        for url in ('https://tidal.com/album/20115556', 'https://www.beatport.com/release/album/123',
                    'https://www.qobuz.com/fr-fr/album/name/0886443927087', 'Artist - Song',
                    'https://soundcloud.com/artist/song', 'https://open.spotify.com/track/4m2880jivSbbyEGAKfITCa'):
            self.assertTrue(engine.identify(url))
        for url in ('https://localhost/private', 'https://tidal.com:8000/track/1',
                    'https://user@tidal.com/track/1', 'http://tidal.com/track/1', 'file:///etc/passwd', None):
            with self.assertRaises(ValueError):
                engine.identify(url)
