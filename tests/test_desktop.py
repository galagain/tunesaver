import asyncio
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer
import test_webapp as fixtures
from test_webapp import musicdl, webapp
from desktop import COOKIE, Desktop, KeychainVault, LOCAL_USER_ID, configure


class MemoryVault:
    def __init__(self):
        self.values = {}

    def get(self, provider):
        return self.values.get(provider, {}).copy()

    def set(self, provider, value):
        self.values[provider] = value.copy()

    def delete(self, provider):
        self.values.pop(provider, None)


class DesktopHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.WebAppHTTPTests.asyncSetUp(self)
        self.root = Path(self.temporary.name)
        (self.root / "logs").mkdir()
        shutil.copytree(fixtures.PROJECT_ROOT / "config", musicdl.CONFIG_ROOT)
        self.desktop = Desktop(self.root, MemoryVault())
        self.worker_mock = mock.patch.object(self.desktop, "start_worker")
        self.worker = self.worker_mock.start()
        self.app = webapp.create_app(self.desktop)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.cookie = {"Cookie": f"{COOKIE}={self.desktop.cookie}"}

    async def asyncTearDown(self):
        await self.client.close()
        self.worker_mock.stop()
        await fixtures.WebAppHTTPTests.asyncTearDown(self)

    async def test_loopback_alone_does_not_grant_access(self):
        for path in ("/", "/api/config", "/api/library", "/api/desktop/accounts"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 401, path)
        response = await self.client.get("/api/library", headers={"Cookie": f"{COOKIE}=wrong"})
        self.assertEqual(response.status, 401)

    async def test_bootstrap_single_use_cookie_and_no_secret_in_config(self):
        url = f"/desktop/start?token={self.desktop.bootstrap}"
        response = await self.client.get(url, allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], "/")
        cookie = response.cookies[COOKIE]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual((await self.client.get(url, allow_redirects=False)).status, 403)
        response = await self.client.get("/api/config", headers=self.cookie)
        data = await response.json()
        self.assertTrue(data["desktop"])
        self.assertNotIn(self.desktop.cookie, json.dumps(data))

    async def test_cross_origin_and_dns_rebinding_are_rejected(self):
        for headers in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            response = await self.client.post("/api/playlists", json={"name": "Injected"}, headers={**self.cookie, **headers})
            self.assertEqual(response.status, 403)
        self.assertEqual(webapp.local_playlists(LOCAL_USER_ID), [])

    async def test_server_account_and_admin_routes_are_disabled(self):
        for path in ("/api/signup", "/api/login", "/api/admin/login", "/api/password-reset/request", "/api/feedback"):
            response = await self.client.post(path, json={}, headers=self.cookie)
            self.assertEqual(response.status, 404, path)

    async def test_library_persists_without_tunesaver_account(self):
        response = await self.client.post("/api/playlists", json={"name": "Mon Mac"}, headers=self.cookie)
        self.assertEqual(response.status, 201)
        response = await self.client.get("/api/library", headers=self.cookie)
        self.assertEqual(response.status, 200)
        with webapp.local_db() as db:
            self.assertEqual(db.execute("select count(*) from users").fetchone()[0], 1)
            self.assertEqual(db.execute("select count(*) from sessions").fetchone()[0], 0)
        self.assertEqual(webapp.local_playlists(LOCAL_USER_ID)[0]["name"], "Mon Mac")

    async def test_desktop_does_not_start_retention_cleanup(self):
        self.assertNotIn(webapp.audio_retention_context, self.app.cleanup_ctx)
        server = webapp.create_app()
        self.assertIn(webapp.audio_retention_context, server.cleanup_ctx)

    async def test_connect_persists_only_valid_credentials_and_disconnect_removes_them(self):
        def login(provider, payload):
            for key in ("username", "password"):
                path = musicdl.SECRETS_ROOT / ("beatport_" + key)
                path.write_text(payload[key])
                path.chmod(0o600)
        with mock.patch.object(self.desktop, "password_login", side_effect=login):
            response = await self.client.post("/api/desktop/accounts/beatport", json={"username": "fixture", "password": "private-value"}, headers=self.cookie)
            self.assertEqual(response.status, 202)
            await self.desktop.tasks["beatport"]
        self.assertEqual(self.desktop.vault.get("beatport")["beatport_password"], "private-value")
        self.worker.assert_called_with("beatport")
        status = await self.client.get("/api/desktop/accounts", headers=self.cookie)
        text = await status.text()
        self.assertNotIn("private-value", text)
        self.assertTrue(json.loads(text)["accounts"]["beatport"]["connected"])
        response = await self.client.delete("/api/desktop/accounts/beatport", json={}, headers=self.cookie)
        self.assertEqual(response.status, 200)
        self.assertFalse(self.desktop.vault.get("beatport"))
        self.assertFalse(self.desktop.connected("beatport"))

    async def test_failed_keychain_save_does_not_start_worker_or_leave_password(self):
        def login(provider, payload):
            (musicdl.SECRETS_ROOT / "beatport_username").write_text("fixture")
            (musicdl.SECRETS_ROOT / "beatport_password").write_text("private-value")
        with mock.patch.object(self.desktop, "password_login", side_effect=login), \
             mock.patch.object(self.desktop.vault, "set", side_effect=RuntimeError("private-value")):
            await self.desktop.connect("beatport", {})
        self.worker.assert_not_called()
        self.assertFalse(self.desktop.connected("beatport"))
        self.assertEqual(self.desktop.jobs["beatport"]["state"], "error")
        self.assertNotIn("private-value", str(self.desktop.jobs))

    async def test_pending_connection_cannot_be_replaced(self):
        task = asyncio.create_task(asyncio.sleep(3600))
        self.desktop.tasks["tiddl"] = task
        response = await self.client.post("/api/desktop/accounts/tiddl", json={}, headers=self.cookie)
        self.assertEqual(response.status, 409)
        response = await self.client.delete("/api/desktop/accounts/tiddl", json={}, headers=self.cookie)
        self.assertEqual(response.status, 200)
        self.assertTrue(task.cancelled())

    async def test_restore_credentials_uses_private_files(self):
        self.desktop.vault.set("tiddl", {"auth.json": json.dumps({"token": "fixture", "refresh_token": "private"})})
        self.desktop.restore()
        self.assertTrue(self.desktop.connected("tiddl"))
        self.assertEqual(webapp.TIDAL_AUTH_PATH.stat().st_mode & 0o777, 0o600)

    async def test_tidal_device_flow_persists_tokens_after_authorization(self):
        responses = [
            mock.Mock(status_code=200, json=lambda: {"deviceCode": "private-device", "verificationUriComplete": "link.tidal.com/TEST", "userCode": "TEST", "expiresIn": 60, "interval": 1}),
            mock.Mock(status_code=200, json=lambda: {"access_token": "private-access", "refresh_token": "private-refresh", "expires_in": 1000, "user_id": 42, "user": {"countryCode": "FR"}}),
        ]
        with mock.patch("requests.post", side_effect=responses), mock.patch("desktop.asyncio.sleep", new=mock.AsyncMock()):
            await self.desktop.connect("tiddl", {})
        self.assertEqual(self.desktop.jobs["tiddl"]["state"], "connected")
        self.assertNotIn("private", str(self.desktop.jobs))
        self.assertIn("private-refresh", self.desktop.vault.get("tiddl")["auth.json"])

    async def test_locked_keychain_does_not_prevent_offline_library(self):
        with mock.patch.object(self.desktop.vault, "get", side_effect=RuntimeError("locked")):
            self.desktop.restore()
        self.assertIn("Trousseau", self.desktop.storage_error)
        response = await self.client.get("/api/library", headers=self.cookie)
        self.assertEqual(response.status, 200)


class DesktopIsolationTests(unittest.TestCase):
    def test_server_environment_is_not_inherited(self):
        with mock.patch.dict(os.environ, {"TUNESAVER_PG_HOST": "server", "MUSICDL_WEB_HOST": "0.0.0.0", "TIDDL_AUTH": "private"}):
            configure(Path("/tmp/local-data"), Path("/tmp/resources"))
            self.assertNotIn("TUNESAVER_PG_HOST", os.environ)
            self.assertNotIn("TIDDL_AUTH", os.environ)
            self.assertEqual(os.environ["TUNESAVER_ALLOWED_HOSTS"], "127.0.0.1")
            self.assertEqual(os.environ["TUNESAVER_DB_PATH"], "/tmp/local-data/library.sqlite")

    def test_keychain_delete_failure_is_not_silenced(self):
        vault = KeychainVault.__new__(KeychainVault)
        vault.backend = mock.Mock()
        vault.backend.get_password.return_value = "exists"
        vault.backend.delete_password.side_effect = RuntimeError("locked")
        with self.assertRaises(RuntimeError):
            vault.delete("tiddl")
