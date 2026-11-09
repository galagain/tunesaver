import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import cli,server_setup,web_login

class CommandTests(unittest.TestCase):
    def test_help_and_version_no_side_effect(self):
        for args in (['--help'],['--version']):
            with contextlib.redirect_stdout(io.StringIO()) as output, mock.patch('subprocess.call') as run:
                self.assertEqual(cli.main(args),0);run.assert_not_called();self.assertIn('TuneSaver',output.getvalue())
    def test_server_help_uses_server_script(self):
        with mock.patch('subprocess.call',return_value=0) as call:
            cli.main(['server','--help'])
        self.assertEqual(call.call_args.args[0][-1],'--help')
        self.assertTrue(call.call_args.args[0][1].endswith('/scripts/server.sh'))
    def test_setup_retains_existing_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'.env').write_text('WEB_PORT=8080\n')
            bundle={'format':'tunesaver-accounts-v1','accounts':{'beatport':{'beatport_username':'test','beatport_password':'first'}}}
            self.assertEqual(server_setup.apply_bundle(root,bundle),['beatport'])
            bundle['accounts']['beatport']['beatport_password']='second'
            self.assertEqual(server_setup.apply_bundle(root,bundle),[])
            self.assertEqual((root/'secrets/beatport_password').read_text(),'first')
            self.assertEqual((root/'secrets/beatport_password').stat().st_mode & 0o777,0o600)
    def test_invalid_file_never_creates_arbitrary_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with self.assertRaises(ValueError):server_setup.apply_bundle(root,{'format':'tunesaver-accounts-v1','accounts':{'beatport':{'../escape':'value'}}})
            self.assertEqual(list(root.iterdir()),[])
    def test_poll_cancelled_before_network(self):
        import threading
        stop=threading.Event();stop.set()
        with mock.patch.object(web_login,'post') as post:
            with self.assertRaises(RuntimeError):web_login.wait({'device_token':'test'},stop)
            post.assert_not_called()
    def test_redirects_refused(self):
        self.assertIsNone(web_login.NoRedirect().redirect_request(None,None,302,'',{},'https://evil.test'))

if __name__=='__main__':unittest.main()
