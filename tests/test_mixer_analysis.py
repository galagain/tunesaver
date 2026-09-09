import contextlib
import base64
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import mixer_analysis


class Error(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class MixerStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.db"
        with self.db() as db:
            db.executescript("""create table users(id text primary key);
              create table download_history(job_id text primary key, user_id text, state text, created_at text);
              create table library_removed(user_id text,job_id text);
              insert into users values('a'),('b');
              insert into download_history values('one','a','done','2026'),('two','b','done','2026');""")
        mixer_analysis.init_store(self.db)
        self.service = mixer_analysis.MixerAnalysis(self.db, lambda job, owner: {"mime_type": "audio/wav", "path": self.path}, Error)

    def tearDown(self):
        self.temp.cleanup()

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys=on")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def test_owner_is_checked_before_delivery_or_settings(self):
        for action in (lambda: self.service.read('b', 'one'), lambda: self.service.patch('b', 'one', {'bpm': 130})):
            with self.assertRaises(Error) as exc:
                action()
            self.assertEqual(exc.exception.status, 404)

    def test_v2_migration_preserves_cues_and_does_not_requeue_current_analysis(self):
        with self.db() as db:
            db.execute("insert into mixer_analysis(job_id,status,result) values('one','done',?)", (json.dumps({'version': 1, 'bpm': 120}),))
            db.execute("insert into mixer_analysis(job_id,status,result) values('two','done',?)", (json.dumps({'version': 2, 'bpm': 128}),))
        self.service.patch('a', 'one', {'hotcue': {'index': 2, 'time': 3}})
        mixer_analysis.init_store(self.db)
        self.assertEqual(self.service.read('a', 'one')['status'], 'pending')
        self.assertEqual(self.service.read('a', 'one')['settings']['hotcues'][2], 3)
        self.assertEqual(self.service.read('b', 'two')['status'], 'done')
        self.assertEqual(mixer_analysis.summaries(self.db, 'b')['two']['bpm'], 128)

    def test_analysis_queue_and_settings_survive_reopening(self):
        self.assertEqual(self.service.read('a', 'one')['status'], 'pending')
        row = self.service.next_job()
        self.service.finish(row['job_id'], 'done', {'bpm': 120, 'key': 'Am', 'duration': 10})
        self.service.patch('a', 'one', {'bpm': 128, 'grid_offset': .25})
        self.service.patch('a', 'one', {'hotcue': {'index': 0, 'time': 2}})
        self.service.patch('a', 'one', {'hotcue': {'index': 1, 'time': 3}})
        self.service.patch('a', 'one', {'hotcue': {'index': 0, 'time': None}})
        reopened = mixer_analysis.MixerAnalysis(self.db, self.service.delivery, Error)
        data = reopened.read('a', 'one')
        self.assertEqual(data['settings']['bpm'], 128)
        self.assertEqual(data['settings']['hotcues'][:2], [None, 3])
        self.assertEqual(mixer_analysis.summaries(self.db, 'a')['one']['bpm'], 128)
        self.assertNotIn('two', mixer_analysis.summaries(self.db, 'a'))

    def test_invalid_values_rejected_without_overwriting(self):
        self.service.patch('a', 'one', {'bpm': 120})
        for payload in ({'bpm': float('nan')}, {'bpm': True}, {'bpm': 999}, {'key': []}, {'grid_offset': -1},
                        {'hotcue': {'index': 8, 'time': 1}}, {'hotcue': {'index': True, 'time': 1}},
                        {'hotcue': {'index': 0, 'time': float('inf')}}, {'hotcues': []},
                        {'tempo_mode': 'auto'}, {'tempo_mode': True}, {'tempo_mode': []}, [], {}):
            with self.assertRaises(Error):
                self.service.patch('a', 'one', payload)
        self.assertEqual(self.service.read('a', 'one')['settings']['bpm'], 120)
        self.service.patch('a', 'one', {'bpm': None})
        self.assertNotIn('bpm', self.service.read('a', 'one')['settings'])

    def test_tempo_mode_persists_without_discarding_analysis_or_manual_edits(self):
        self.service.next_job()
        analysis = {'version': 2, 'bpm': 127.5, 'grid_quality': 'variable', 'beats': [0, .47, .95], 'duration': 10}
        self.service.finish('one', 'done', analysis)
        self.assertEqual(mixer_analysis.summaries(self.db, 'a')['one']['tempo_mode'], 'fixed')
        self.service.patch('a', 'one', {'bpm': 128, 'grid_offset': .1, 'hotcue': {'index': 0, 'time': 2}})
        self.service.patch('a', 'one', {'tempo_mode': 'variable'})
        mixer_analysis.init_store(self.db)
        reopened = mixer_analysis.MixerAnalysis(self.db, self.service.delivery, Error)
        data = reopened.read('a', 'one')
        self.assertEqual(data['analysis'], analysis)
        self.assertEqual(data['settings']['tempo_mode'], 'variable')
        self.assertEqual(data['settings']['bpm'], 128)
        self.assertEqual(mixer_analysis.summaries(self.db, 'a')['one']['bpm'], 127.5)
        self.assertEqual(data['settings']['hotcues'][0], 2)
        self.service.finish('one', 'done', {**analysis, 'bpm': 127.6})
        self.assertEqual(reopened.read('a', 'one')['settings']['tempo_mode'], 'variable')
        self.service.patch('a', 'one', {'tempo_mode': 'fixed'})
        self.assertEqual(mixer_analysis.summaries(self.db, 'a')['one']['tempo_mode'], 'fixed')
        self.service.patch('a', 'one', {'tempo_mode': None})
        self.assertNotIn('tempo_mode', reopened.read('a', 'one')['settings'])
        with self.assertRaises(Error):
            self.service.patch('b', 'one', {'tempo_mode': 'variable'})

    def test_removed_tracks_are_not_automatically_scheduled(self):
        with self.db() as db:
            db.execute("insert into library_removed values('a','one')")
        self.assertEqual(self.service.next_job()['job_id'], 'two')
        self.assertIsNone(self.service.next_job())

    def test_cues_respect_analyzed_duration(self):
        with self.db() as db:
            db.execute("insert into mixer_analysis(job_id,status,result) values('one','done',?)", (json.dumps({'duration': 10}),))
        with self.assertRaises(Error):
            self.service.patch('a', 'one', {'hotcue': {'index': 0, 'time': 12}})


class AudioAnalysisTests(unittest.TestCase):
    def test_long_fractional_tempo_with_silent_break(self):
        import numpy as np
        import soundfile as sf
        from analyze_audio import analyze
        sr, duration, bpm, offset = 22050, 90, 127.5, .73
        audio = np.zeros(sr * duration)
        pulse = np.sin(2 * np.pi * 90 * np.arange(1500) / sr) * np.exp(-np.arange(1500) / 250)
        for start in np.arange(offset, duration - 1, 60 / bpm):
            if 30 < start < 36:
                continue
            i = int(start * sr)
            audio[i:i + len(pulse)] += pulse * .85
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'long.wav'
            sf.write(path, audio, sr)
            result = analyze(path)
        self.assertAlmostEqual(result['bpm'], bpm, delta=.02)
        step = 60 / bpm
        phase_error = ((result['grid_offset'] - offset + step / 2) % step) - step / 2
        self.assertLess(abs(phase_error), .02)

    def test_real_click_track_and_key(self):
        import numpy as np
        import soundfile as sf
        from analyze_audio import analyze
        with tempfile.TemporaryDirectory() as folder:
            sr = 22050
            t = np.arange(sr * 24) / sr
            audio = sum(np.sin(2 * np.pi * frequency * t) for frequency in (261.6256, 329.6276, 391.9954)) * .04
            for start in np.arange(.25, 24, .5):
                i = int(start * sr)
                pulse = np.sin(2 * np.pi * 100 * np.arange(800) / sr) * np.exp(-np.arange(800) / 180)
                audio[i:i + len(pulse)] += pulse[:len(audio[i:i + len(pulse)])] * .8
            path = Path(folder) / 'fixture.wav'
            sf.write(path, audio, sr)
            process = subprocess.run([sys.executable, str(Path(mixer_analysis.__file__).with_name('analyze_audio.py')), str(path)], capture_output=True, text=True, timeout=120)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            result = json.loads(process.stdout)
            self.assertAlmostEqual(result['duration'], 24, delta=.01)
            self.assertAlmostEqual(result['bpm'], 120, delta=.05)
            self.assertAlmostEqual(result['grid_offset'], .25, delta=.025)
            self.assertIn(result['key'], ('C', 'Am'))
            self.assertGreater(len(result['beats']), 30)
            self.assertEqual(result['engine'], 'Queen Mary DSP')
            self.assertEqual(result['waveform']['rate'], 441)
            packed = base64.b64decode(result['waveform']['data'])
            self.assertEqual(len(packed), result['waveform']['frames'] * 5)
            self.assertGreater(max(packed[3::5]), 100)
            sf.write(path, np.zeros(sr * 3), sr)
            silence = analyze(path)
            self.assertIsNone(silence['bpm'])
            self.assertIsNone(silence['key'])
