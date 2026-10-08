from pathlib import Path
import tempfile
import unittest
from stream_audio import playback_file


def block(kind, data, last=False):
    return bytes([kind | (128 if last else 0)]) + len(data).to_bytes(3, 'big') + data


class PlaybackCopyTests(unittest.TestCase):
    def test_audio_frames_and_seek_table_unchanged_and_original_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'track.flac'
            frames = b'original-audio-frames' * 100
            info, seek = b'info' * 8 + b'00', b'seek-table'
            original = b'fLaC' + block(0, info) + block(6, b'large-picture') + block(3, seek) + block(1, b'padding', True) + frames
            path.write_bytes(original)
            result = playback_file(path)
            self.assertNotEqual(result, path)
            self.assertEqual(result.read_bytes(), b'fLaC' + block(0, info) + block(3, seek, True) + frames)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(playback_file(path), result)
            self.assertEqual(len(list(path.parent.glob('.stream-*'))), 1)

    def test_invalid_and_already_minimal_files_are_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'track.flac'
            for content in (b'not-flac', b'fLaC\x80\xff\xff\xff', b'fLaC' + block(0, b'info', True) + b'audio'):
                path.write_bytes(content)
                self.assertEqual(playback_file(path), path)
                self.assertEqual(path.read_bytes(), content)
