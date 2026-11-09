import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import server_database

class DatabaseCommandsTests(unittest.TestCase):
    def test_setup_refuses_existing_sqlite(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);db=root/'data/state/tiddl/tunesaver.db';db.parent.mkdir(parents=True);db.write_bytes(b'existing')
            with mock.patch('subprocess.call') as call, self.assertRaises(RuntimeError):server_database.main(root,['setup'])
            call.assert_not_called();self.assertFalse((root/'.env.database').exists());self.assertEqual(db.read_bytes(),b'existing')
    def test_setup_is_private_and_does_not_rotate_credentials(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch('subprocess.call',return_value=0):
            root=Path(folder);server_database.main(root,['setup']);config=(root/'.env.database').read_text();password=(root/'secrets/postgres_password').read_text()
            server_database.main(root,['setup']);self.assertEqual((root/'.env.database').read_text(),config);self.assertEqual((root/'secrets/postgres_password').read_text(),password)
            self.assertEqual((root/'.env.database').stat().st_mode&0o777,0o600)
    def test_failed_backup_never_looks_complete(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch('subprocess.run',side_effect=subprocess.CalledProcessError(1,['pg_dump'])):
            root=Path(folder)
            with self.assertRaises(subprocess.CalledProcessError):server_database.main(root,['backup'])
            self.assertEqual(list((root/'data/backups').iterdir()),[])
    def test_backup_is_private(self):
        def write(*args,**kw):kw['stdout'].write(b'PGDMP')
        with tempfile.TemporaryDirectory() as folder, mock.patch('subprocess.run',side_effect=write),contextlib.redirect_stdout(io.StringIO()):
            root=Path(folder);self.assertEqual(server_database.main(root,['backup']),0)
            files=list((root/'data/backups').iterdir());self.assertEqual(len(files),1);self.assertEqual(files[0].read_bytes(),b'PGDMP');self.assertEqual(files[0].stat().st_mode&0o777,0o600)
