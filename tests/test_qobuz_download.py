import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import qobuz_download as q

class Transfers(unittest.TestCase):
    def response(self,chunks,length):
        r=mock.MagicMock();r.__enter__.return_value=r;r.headers={'content-length':str(length)}
        r.iter_content.return_value=iter(chunks);return r
    def test_short_response_retried_atomically(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'track.flac';p.write_bytes(b'old')
            with mock.patch.object(q.requests,'get',side_effect=[self.response([b'x'],4),self.response([b'abcd'],4)]) as get,mock.patch.object(q.time,'sleep'):
                q.download('https://example.test/audio?secret=hidden',p,'track')
            self.assertEqual(p.read_bytes(),b'abcd');self.assertEqual(get.call_count,2);self.assertEqual(list(Path(folder).iterdir()),[p])
    def test_failed_retries_preserve_existing_file_and_hide_url(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'track.flac';p.write_bytes(b'old')
            with mock.patch.object(q.requests,'get',side_effect=requests.ConnectionError('secret-url')) as get,mock.patch.object(q.time,'sleep'):
                with self.assertRaises(RuntimeError) as error:q.download('secret-url',p,'track')
            self.assertNotIn('secret-url',str(error.exception));self.assertEqual(get.call_count,3);self.assertEqual(p.read_bytes(),b'old');self.assertEqual(list(Path(folder).iterdir()),[p])
    def test_permanent_http_error_not_retried(self):
        r=self.response([],0);r.status_code=403;r.raise_for_status.side_effect=requests.HTTPError(response=r)
        with tempfile.TemporaryDirectory() as folder,mock.patch.object(q.requests,'get',return_value=r) as get:
            with self.assertRaisesRegex(RuntimeError,'HTTP 403'):q.download('url',Path(folder)/'audio','track')
            self.assertEqual(get.call_count,1);self.assertEqual(list(Path(folder).iterdir()),[])
