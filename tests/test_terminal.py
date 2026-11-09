import asyncio
import contextlib
import fcntl
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

import test_webapp as fixtures
from test_webapp import musicdl, webapp
from test_desktop import MemoryVault
from local_session import LocalSession
import terminal

sys.path.insert(0, str(fixtures.PROJECT_ROOT / "desktop"))
import install_cli


class TerminalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.WebAppHTTPTests.asyncSetUp(self)
        self.root = Path(self.temporary.name)
        shutil.copytree(fixtures.PROJECT_ROOT / "config", musicdl.CONFIG_ROOT)
        self.accounts = terminal.make_accounts(self.root, MemoryVault())

    async def asyncTearDown(self):
        await fixtures.WebAppHTTPTests.asyncTearDown(self)

    def authenticate(self):
        musicdl.atomic_write_json(webapp.TIDAL_AUTH_PATH,
            {"token": "private-access", "refresh_token": "private-refresh"})

    def test_shortcuts_and_paths_with_spaces(self):
        args = terminal.build_parser().parse_args(terminal.normalize_argv(
            ["--data-dir", "/tmp/private data", "https://tidal.com/track/123", "-o", "/tmp/mon set"]))
        self.assertEqual(args.command, "download")
        self.assertEqual(args.output, Path("/tmp/mon set"))
        self.assertEqual(terminal.normalize_argv(["artiste titre"]), ["search", "artiste titre"])

    def test_tidal_config_preserves_unicode_quotes_and_backslashes(self):
        output = self.root / 'Mon set été 🎶 "live" \\1'
        musicdl.sync_tiddl_config(output)
        config = tomllib.loads((musicdl.STATE_ROOT / "tiddl/config.toml").read_text())
        self.assertEqual(config["download"]["download_path"], str(output))
        self.assertEqual(config["download"]["scan_path"], str(output))

    def test_only_native_platform_links(self):
        for url, provider in (("https://tidal.com/track/123", "tiddl"),
                              ("https://www.beatport.com/track/test/123", "beatport"),
                              ("https://www.qobuz.com/fr-fr/album/test/123", "qobuz")):
            self.assertEqual(terminal.detect_provider(url)[0], provider)
        for url in ("https://tidal.com.evil.test/track/123", "file:///etc/passwd", "http://tidal.com/track/123"):
            with self.assertRaises(ValueError):
                terminal.detect_provider(url)

    def test_download_writes_to_selected_directory_and_saves_refreshed_auth(self):
        self.authenticate()
        output = self.root / "Musique été"
        def download(provider, urls, log, *, output_dir):
            self.assertEqual(provider, "tiddl")
            self.assertEqual(len(urls), 1)
            (output_dir / "titre.wav").write_bytes(b"RIFF-fixture")
            musicdl.atomic_write_json(webapp.TIDAL_AUTH_PATH,
                {"token": "private-renewed", "refresh_token": "private-refresh"})
            return 0
        stdout = io.StringIO()
        with mock.patch.object(musicdl, "_run_provider_unlocked", side_effect=download), contextlib.redirect_stdout(stdout):
            code = terminal.download(self.accounts, ["https://tidal.com/track/123"] * 2, output)
        self.assertEqual(code, 0)
        self.assertEqual((output / "titre.wav").read_bytes(), b"RIFF-fixture")
        self.assertIn("private-renewed", self.accounts.vault.get("tiddl")["auth.json"])
        self.assertNotIn("private-renewed", stdout.getvalue())
        self.assertNotIn("private-refresh", stdout.getvalue())
        self.assertEqual(self.accounts.workers, {})

    def test_noninteractive_download_requires_connection(self):
        stderr = io.StringIO()
        with mock.patch("sys.stdin.isatty", return_value=False), contextlib.redirect_stderr(stderr), \
             mock.patch.object(musicdl, "_run_provider_unlocked") as downloader:
            self.assertEqual(terminal.download(self.accounts, ["track/123"], self.root / "output"), 1)
        downloader.assert_not_called()
        self.assertIn("tunesaver login tidal", stderr.getvalue())

    def test_all_links_are_validated_before_first_download(self):
        self.authenticate()
        with mock.patch.object(musicdl, "_run_provider_unlocked") as downloader, self.assertRaises(ValueError):
            terminal.download(self.accounts, ["track/123", "https://evil.test/track/123"], self.root)
        downloader.assert_not_called()

    def test_download_failure_is_nonzero(self):
        self.authenticate()
        with mock.patch.object(musicdl, "_run_provider_unlocked", return_value=7), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(terminal.download(self.accounts, ["track/123"], self.root), 1)

    def test_logout_cannot_remove_credentials_used_by_another_process(self):
        self.authenticate()
        self.accounts.persist("tiddl")
        with musicdl.provider_lock("tiddl", "operation", blocking=False), self.assertRaises(RuntimeError):
            terminal.logout(self.accounts, "tiddl")
        self.assertTrue(self.accounts.connected("tiddl"))
        self.assertTrue(self.accounts.vault.get("tiddl"))

    async def test_connect_lock_conflict_preserves_other_client_credentials(self):
        self.authenticate()
        with musicdl.provider_lock("tiddl", "operation", blocking=False):
            await self.accounts.connect("tiddl", {})
        self.assertTrue(self.accounts.connected("tiddl"))
        self.assertEqual(self.accounts.jobs["tiddl"]["state"], "error")

    async def test_device_login_prints_authorization_without_starting_gui(self):
        async def authorize():
            self.accounts.jobs["tiddl"]["authorization_url"] = "https://link.tidal.com/TEST"
            await asyncio.sleep(.25)
            self.authenticate()
        stdout = io.StringIO()
        with mock.patch.object(self.accounts, "tidal_login", side_effect=authorize), contextlib.redirect_stdout(stdout):
            self.assertEqual(await terminal.login_async(self.accounts, "tiddl", {}), 0)
        self.assertIn("https://link.tidal.com/TEST", stdout.getvalue())
        self.assertNotIn("private", stdout.getvalue())
        self.assertEqual(self.accounts.workers, {})

    def test_session_survives_until_last_client_exits(self):
        initialize = mock.Mock(side_effect=self.authenticate)
        first = LocalSession(self.root, initialize)
        second = LocalSession(self.root, initialize, terminal=True)
        first.__enter__()
        second.__enter__()
        try:
            initialize.assert_called_once()
            first.__exit__(None, None, None)
            self.assertTrue(self.accounts.connected("tiddl"))
        finally:
            second.__exit__(None, None, None)
        self.assertFalse(musicdl.STATE_ROOT.exists())

    def test_stale_session_is_cleaned_without_touching_music(self):
        stale = musicdl.STATE_ROOT / "stale"
        stale.write_text("private")
        music = self.root / "Music"
        music.mkdir()
        (music / "keep.flac").write_bytes(b"fixture")
        with LocalSession(self.root, self.authenticate, terminal=True):
            self.assertFalse(stale.exists())
            self.assertTrue(self.accounts.connected("tiddl"))
        self.assertTrue((music / "keep.flac").exists())

    def test_old_gui_is_not_cleaned_by_new_cli(self):
        self.authenticate()
        with (self.root / ".desktop.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(RuntimeError, "ancienne version"):
                with LocalSession(self.root, self.authenticate, terminal=True):
                    self.fail("Must not join an old GUI session")
        self.assertTrue(self.accounts.connected("tiddl"))

    def test_json_search_persists_tokens_without_interactive_prompt(self):
        self.authenticate()
        result = [{"provider": "tiddl", "title": "Fixture", "url": "https://tidal.com/track/123"}]
        stdout = io.StringIO()
        with mock.patch.object(webapp, "search_tidal", return_value=result), \
             mock.patch("builtins.input") as prompt, contextlib.redirect_stdout(stdout):
            self.assertEqual(terminal.search(self.accounts, "Fixture", as_json=True), 0)
        prompt.assert_not_called()
        self.assertEqual(json.loads(stdout.getvalue()), result)
        self.assertIn("private-refresh", self.accounts.vault.get("tiddl")["auth.json"])


class InstallerTests(unittest.TestCase):
    def payload(self, root):
        resources = root / "Application avec espaces"
        resources.mkdir()
        for name in install_cli.PAYLOAD:
            if "." in name or name == "tunesaver":
                (resources / name).write_text("fixture")
            else:
                (resources / name).mkdir()
                (resources / name / "payload").write_text("fixture")
        (resources / ".env").write_text("must-not-copy")
        return resources

    def test_install_is_independent_and_upgrade_preserves_running_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.payload(root)
            destination, bin_dir = root / "CLI", root / "bin"
            entry = install_cli.install(source, destination, bin_dir)
            old = entry.resolve()
            self.assertTrue(old.stat().st_mode & 0o111)
            self.assertFalse((old.parent / ".env").exists())
            install_cli.install(source, destination, bin_dir)
            self.assertNotEqual(old, entry.resolve())
            shutil.rmtree(source)
            self.assertEqual(entry.read_text(), "fixture")
            self.assertTrue(old.is_file())

    def test_install_refuses_unrelated_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.payload(root)
            (root / "bin").mkdir()
            entry = root / "bin/tunesaver"
            entry.write_text("user-script")
            with self.assertRaises(RuntimeError):
                install_cli.install(source, root / "CLI", root / "bin")
            self.assertEqual(entry.read_text(), "user-script")
