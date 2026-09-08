from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import musicdl  # noqa: E402
import webapp  # noqa: E402

TEST_USER_ID = "11111111-1111-4111-8111-111111111111"
TEST_TOKEN = "test-access-token"


class FakeRequest:
    def __init__(
        self,
        payload: dict | None = None,
        *,
        method: str = "POST",
        path: str = "/api/test",
        headers: dict[str, str] | None = None,
        app: dict | None = None,
        match_info: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.method = method
        self.path = path
        self.headers = {
            "Host": "127.0.0.1:8080",
            "Authorization": f"Bearer {TEST_TOKEN}",
            **(headers or {}),
        }
        self.content_type = "application/json" if method in {"POST", "PUT", "PATCH", "DELETE"} else ""
        self.app = app or {}
        self.match_info = match_info or {}
        self.query = query or {}
        self._state = {"user": {"id": TEST_USER_ID, "email": "test@example.com"}}

    async def json(self, *, loads=json.loads):
        return self._payload

    def __getitem__(self, key):
        return self._state[key]

    def __setitem__(self, key, value):
        self._state[key] = value

    def get(self, key, default=None):
        return self._state.get(key, default)


def response_json(response) -> dict:
    return json.loads(response.text)


class WebAppFunctionTests(unittest.TestCase):
    def test_local_password_hash_verifies_only_matching_password(self) -> None:
        salt, digest = webapp.hash_password("secret123")
        self.assertTrue(webapp.verify_password("secret123", salt, digest))
        self.assertFalse(webapp.verify_password("wrong", salt, digest))

    def test_admin_daily_stats_fill_days_and_sum_done_jobs(self) -> None:
        today = webapp.date.today()
        yesterday = today - webapp.timedelta(days=1)
        rows = [
            {
                "created_at": today.isoformat() + "T12:00:00Z",
                "state": "done",
                "track_count": 3,
                "estimated_savings_cents": 450,
            },
            {
                "created_at": today.isoformat() + "T13:00:00Z",
                "state": "failed",
                "track_count": 9,
                "estimated_savings_cents": 999,
            },
            {
                "created_at": yesterday.isoformat() + "T09:00:00Z",
                "state": "done",
                "track_count": 0,
                "estimated_savings_cents": 129,
            },
        ]
        daily = webapp.normalize_admin_daily_stats(rows, days=2)
        self.assertEqual([item["date"] for item in daily], [
            yesterday.isoformat(),
            today.isoformat(),
        ])
        self.assertEqual(daily[0]["tracks"], 1)
        self.assertEqual(daily[0]["savings_cents"], 129)
        self.assertEqual(daily[1]["tracks"], 3)
        self.assertEqual(daily[1]["savings_cents"], 450)

    def test_direct_links_are_detected_and_revalidated(self) -> None:
        tidal = webapp.direct_result("https://tidal.com/browse/track/123")
        self.assertIsNotNone(tidal)
        assert tidal is not None
        self.assertEqual(tidal["provider"], "tiddl")
        self.assertEqual(tidal["kind"], "track")

        beatport = webapp.direct_result(
            "https://www.beatport.com/track/example/456"
        )
        self.assertIsNotNone(beatport)
        assert beatport is not None
        self.assertEqual(beatport["provider"], "beatport")
        qobuz = webapp.direct_result("https://play.qobuz.com/track/789")
        self.assertIsNotNone(qobuz)
        assert qobuz is not None
        self.assertEqual(qobuz["provider"], "qobuz")
        self.assertIsNone(webapp.direct_result("https://example.com/track/456"))

    def test_tidal_results_are_normalized_and_unavailable_tracks_filtered(self) -> None:
        payload = {
            "tracks": {
                "items": [
                    {
                        "id": 123,
                        "title": "Example",
                        "duration": 187,
                        "allowStreaming": True,
                        "streamReady": True,
                        "audioQuality": "LOSSLESS",
                        "artists": [{"name": "Artist"}],
                        "album": {
                            "title": "Album",
                            "cover": "12345678-1234-1234-1234-123456789abc",
                        },
                    },
                    {
                        "id": 999,
                        "title": "Unavailable",
                        "allowStreaming": False,
                        "streamReady": True,
                    },
                ]
            }
        }
        results = webapp.normalize_tidal_results(payload)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Example")
        self.assertEqual(results[0]["artist"], "Artist")
        self.assertEqual(results[0]["album"], "Album")
        self.assertEqual(results[0]["quality"], "LOSSLESS")
        self.assertTrue(results[0]["cover_url"].endswith("/320x320.jpg"))
        self.assertIn("3:07", results[0]["details"])

    def test_tidal_top_hit_is_included_and_null_tracks_are_safe(self) -> None:
        track = {
            "id": 321,
            "title": "Top result",
            "allowStreaming": True,
            "streamReady": True,
            "artists": [{"name": "Artist"}],
        }
        results = webapp.normalize_tidal_results(
            {"topHit": {"type": "TRACKS", "value": track}, "tracks": None}
        )
        self.assertEqual([result["id"] for result in results], ["tiddl:track:321"])

    def test_query_normalizes_tiddl_short_resource_case(self) -> None:
        self.assertEqual(webapp.normalized_query("Track/123"), "track/123")

    def test_beatport_results_are_normalized(self) -> None:
        payload = {
            "tracks": [
                {
                    "id": 456,
                    "name": "Example",
                    "mix_name": "Extended Mix",
                    "slug": "example",
                    "bpm": 128,
                    "artists": [{"name": "Producer"}],
                    "release": {
                        "name": "Release",
                        "image": {"uri": "https://img.example/cover.jpg"},
                    },
                    "label": {"name": "Label"},
                    "key": {"name": "A Minor"},
                }
            ]
        }
        result = webapp.normalize_beatport_results(payload)[0]
        self.assertEqual(result["provider"], "beatport")
        self.assertEqual(result["artist"], "Producer")
        self.assertEqual(result["album"], "Release")
        self.assertEqual(result["label"], "Label")
        self.assertEqual(result["bpm"], "128 BPM")
        self.assertEqual(result["key"], "A Minor")
        self.assertEqual(result["cover_url"], "https://img.example/cover.jpg")
        self.assertIn("128 BPM", result["details"])
        self.assertTrue(result["download_url"].endswith("/example/456"))

    def test_fnv_login_id_matches_go_fnv1a(self) -> None:
        self.assertEqual(
            webapp.fnv1a_login_id("user", "pass"),
            "fd7641a046219a51",
        )

    def test_qobuz_results_are_normalized(self) -> None:
        payload = {
            "tracks": {"items": [{
                "id": 789,
                "title": "Example",
                "duration": 245,
                "streamable": True,
                "hires_streamable": True,
                "maximum_bit_depth": 24,
                "maximum_sampling_rate": 96,
                "performer": {"name": "Artist"},
                "album": {
                    "title": "Album",
                    "label": {"name": "Qobuz Label"},
                    "image": {"large": "https://img.example/qobuz.jpg"},
                },
            }]}
        }
        result = webapp.normalize_qobuz_results(payload)[0]
        self.assertEqual(result["provider"], "qobuz")
        self.assertEqual(result["artist"], "Artist")
        self.assertEqual(result["album"], "Album")
        self.assertEqual(result["label"], "Qobuz Label")
        self.assertEqual(result["cover_url"], "https://img.example/qobuz.jpg")
        self.assertIn("Hi-Res", result["details"])
        self.assertTrue(result["download_url"].endswith("/789"))

    def test_beatport_fast_path_falls_back_on_forbidden(self) -> None:
        response = mock.Mock(status_code=403)
        with mock.patch.object(webapp.requests, "get", return_value=response) as get:
            self.assertIsNone(webapp.beatport_api_search("artist title", "token"))
        kwargs = get.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        self.assertIn("Chrome/123", kwargs["headers"]["User-Agent"])


class WebAppHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.originals = {
            "QUEUE_ROOT": musicdl.QUEUE_ROOT,
            "STATE_ROOT": musicdl.STATE_ROOT,
            "CONFIG_ROOT": musicdl.CONFIG_ROOT,
            "SECRETS_ROOT": musicdl.SECRETS_ROOT,
            "TIDDL_DOWNLOAD_DIR": musicdl.TIDDL_DOWNLOAD_DIR,
            "BEATPORT_DOWNLOAD_DIR": musicdl.BEATPORT_DOWNLOAD_DIR,
            "QOBUZ_DOWNLOAD_DIR": musicdl.QOBUZ_DOWNLOAD_DIR,
            "DELIVERY_ROOT": musicdl.DELIVERY_ROOT,
            "QOBUZ_CONFIG_PATH": musicdl.QOBUZ_CONFIG_PATH,
            "TIDAL_AUTH_PATH": webapp.TIDAL_AUTH_PATH,
            "BEATPORT_CACHE_PATH": webapp.BEATPORT_CACHE_PATH,
            "LOCAL_DB_PATH": webapp.LOCAL_DB_PATH,
            "ASSET_ROOT": webapp.ASSET_ROOT,
        }
        musicdl.QUEUE_ROOT = root / "queue"
        musicdl.STATE_ROOT = root / "state"
        musicdl.CONFIG_ROOT = root / "config"
        musicdl.SECRETS_ROOT = root / "secrets"
        musicdl.TIDDL_DOWNLOAD_DIR = root / "downloads" / "tiddl"
        musicdl.BEATPORT_DOWNLOAD_DIR = root / "downloads" / "beatport"
        musicdl.QOBUZ_DOWNLOAD_DIR = root / "downloads" / "qobuz"
        musicdl.DELIVERY_ROOT = root / "deliveries"
        musicdl.QOBUZ_CONFIG_PATH = root / "state" / "qobuz" / ".config" / "qobuz-dl" / "config.ini"
        webapp.TIDAL_AUTH_PATH = musicdl.STATE_ROOT / "tiddl" / "auth.json"
        webapp.BEATPORT_CACHE_PATH = (
            musicdl.STATE_ROOT / "beatportdl" / "beatportdl-credentials.json"
        )
        webapp.LOCAL_DB_PATH = musicdl.STATE_ROOT / "tiddl" / "tunesaver.db"
        webapp.ASSET_ROOT = PROJECT_ROOT / "web"
        musicdl.ensure_layout()
        webapp.init_local_store()
        musicdl.SECRETS_ROOT.mkdir(parents=True)
        self.worker_handles = {
            provider: musicdl.acquire_provider_lock(
                provider, "worker", blocking=False
            )
            for provider in musicdl.PROVIDERS
        }

        self.app_state = {
            webapp.SEARCH_SEMAPHORES_KEY: {
                "tiddl": asyncio.Semaphore(1),
                "beatport": asyncio.Semaphore(1),
                "qobuz": asyncio.Semaphore(1),
            },
            webapp.STATUS_CACHE_KEY: {
                "created_at": 0.0,
                "value": None,
                "lock": asyncio.Lock(),
            },
            webapp.AUTH_CACHE_KEY: {
                hashlib.sha256(TEST_TOKEN.encode()).hexdigest(): (
                    time.monotonic() + 3600,
                    {"id": TEST_USER_ID, "email": "test@example.com"},
                )
            },
            webapp.ADMIN_SESSIONS_KEY: {"admin-token": time.monotonic() + 3600},
        }

    async def asyncTearDown(self) -> None:
        for handle in self.worker_handles.values():
            musicdl.release_provider_lock(handle)
        for name, value in self.originals.items():
            if hasattr(musicdl, name):
                setattr(musicdl, name, value)
            else:
                setattr(webapp, name, value)
        webapp.TIDAL_AUTH_PATH = self.originals["TIDAL_AUTH_PATH"]
        webapp.BEATPORT_CACHE_PATH = self.originals["BEATPORT_CACHE_PATH"]
        webapp.LOCAL_DB_PATH = self.originals["LOCAL_DB_PATH"]
        webapp.ASSET_ROOT = self.originals["ASSET_ROOT"]
        self.temporary.cleanup()

    async def test_index_has_security_headers(self) -> None:
        request = FakeRequest(method="GET", path="/", app=self.app_state)
        response = await webapp.api_middleware(request, webapp.index_handler)
        self.assertEqual(response.status, 200)
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        nonce = request["style_nonce"]
        self.assertIn(f"'nonce-{nonce}'", response.headers["Content-Security-Policy"])
        self.assertIn(nonce, response.text)
        self.assertNotIn("__MIXER_STYLE_NONCE__", response.text)
        self.assertIn("media-src 'self' blob:", response.headers["Content-Security-Policy"])
        self.assertIn("midi=(self)", response.headers["Permissions-Policy"])
        second = FakeRequest(method="GET", path="/", app=self.app_state)
        await webapp.api_middleware(second, webapp.index_handler)
        self.assertNotEqual(nonce, second["style_nonce"])
        index = (PROJECT_ROOT / "web" / "index.html").read_text()
        script = (PROJECT_ROOT / "web" / "app.js").read_text()
        self.assertIn("TuneSaver", index)
        for provider in ("tiddl", "beatport", "qobuz"):
            self.assertIn(f'id="{provider}-health"', index)
        self.assertIn("setServiceStatus(provider", script)

    async def test_mixer_assets_are_public_but_library_stays_private(self) -> None:
        async with TestClient(TestServer(webapp.create_app())) as client:
            for path in ("/mixer.js", "/flx4.js", "/mixer.css", "/vendor/mixer-icons.js", "/vendor/wavesurfer/wavesurfer.esm.js"):
                response = await client.get(path)
                self.assertEqual(response.status, 200, path)
                self.assertGreater(len(await response.read()), 100)
            response = await client.get("/api/library")
            self.assertEqual(response.status, 401)
            response = await client.get("/vendor/../../.env")
            self.assertNotEqual(response.status, 200)

    async def test_direct_search_and_enqueue_are_end_to_end(self) -> None:
        request = FakeRequest(
            {"query": "https://tidal.com/browse/track/123"},
            path="/api/search",
            app=self.app_state,
        )
        response = await webapp.api_middleware(request, webapp.search_handler)
        self.assertEqual(response.status, 200)
        result = response_json(response)["results"][0]

        request = FakeRequest(
            {
                "provider": result["provider"],
                "url": result["download_url"],
                "title": "Example track",
                "artist": "Example artist",
            },
            path="/api/download",
            app=self.app_state,
        )
        first = await webapp.api_middleware(request, webapp.download_handler)
        self.assertEqual(first.status, 202)
        first_payload = response_json(first)
        job_path = (
            musicdl.QUEUE_ROOT
            / "pending"
            / "tiddl"
            / f"{first_payload['job_id']}.json"
        )
        self.assertTrue(job_path.is_file())
        job = json.loads(job_path.read_text(encoding="utf-8"))
        self.assertEqual(job["display"]["title"], "Example track")
        self.assertEqual(job["display"]["artist"], "Example artist")

        request = FakeRequest(
            {"provider": result["provider"], "url": result["download_url"]},
            path="/api/download",
            app=self.app_state,
        )
        duplicate = await webapp.api_middleware(request, webapp.download_handler)
        self.assertEqual(duplicate.status, 200)
        self.assertTrue(response_json(duplicate)["duplicate"])

    async def test_text_search_uses_requested_provider(self) -> None:
        fake = [
            {
                "id": "tiddl:track:1",
                "provider": "tiddl",
                "platform": "TIDAL",
                "kind": "track",
                "title": "Found",
                "artist": "Artist",
                "details": "Album",
                "source_url": "https://listen.tidal.com/track/1",
                "download_url": "https://listen.tidal.com/track/1",
            }
        ]
        with mock.patch.object(webapp, "search_tidal", return_value=fake) as search:
            request = FakeRequest(
                {"query": "artist title", "provider": "tiddl"},
                path="/api/search",
                app=self.app_state,
            )
            response = await webapp.api_middleware(request, webapp.search_handler)
        self.assertEqual(response.status, 200)
        self.assertEqual(response_json(response)["results"], fake)
        search.assert_called_once_with("artist title")

    async def test_cross_origin_mutation_is_rejected(self) -> None:
        request = FakeRequest(
            {"provider": "tiddl", "url": "track/123"},
            path="/api/download",
            headers={
                "Host": "127.0.0.1:8080",
                "Origin": "https://attacker.invalid",
            },
            app=self.app_state,
        )
        response = await webapp.api_middleware(request, webapp.download_handler)
        self.assertEqual(response.status, 403)

    async def test_api_requires_a_local_session(self) -> None:
        request = FakeRequest(
            method="GET",
            path="/api/status",
            headers={"Authorization": ""},
            app=self.app_state,
        )
        response = await webapp.api_middleware(request, webapp.status_handler)
        self.assertEqual(response.status, 401)

    async def test_queue_history_is_isolated_per_user(self) -> None:
        other_user = "22222222-2222-4222-8222-222222222222"
        musicdl.enqueue(
            "tiddl",
            ["track/123"],
            owner_id=TEST_USER_ID,
            ephemeral=True,
        )
        musicdl.enqueue(
            "tiddl",
            ["track/456"],
            owner_id=other_user,
            ephemeral=True,
        )
        snapshot = webapp.queue_snapshot(TEST_USER_ID)
        self.assertEqual(snapshot["counts"]["tiddl"]["pending"], 1)
        self.assertEqual(len(snapshot["recent"]), 1)

    async def test_batch_download_accepts_one_link_per_line(self) -> None:
        request = FakeRequest(
            {
                "links": [
                    "https://tidal.com/track/123",
                    "https://play.qobuz.com/track/456",
                ],
                "confirm_complete": True,
            },
            path="/api/downloads",
            app=self.app_state,
        )
        response = await webapp.api_middleware(
            request, webapp.batch_download_handler
        )
        self.assertEqual(response.status, 202)
        self.assertEqual(len(response_json(response)["jobs"]), 2)

    async def test_dns_rebinding_host_is_rejected(self) -> None:
        request = FakeRequest(
            {"query": "artist title", "provider": "tiddl"},
            path="/api/search",
            headers={"Host": "evil.example", "Origin": "http://evil.example"},
            app=self.app_state,
        )
        response = await webapp.api_middleware(request, webapp.search_handler)
        self.assertEqual(response.status, 403)

    async def test_unsupported_url_is_not_used_as_search_text(self) -> None:
        request = FakeRequest(
            {"query": "https://example.com/not-supported"},
            path="/api/search",
            app=self.app_state,
        )
        with mock.patch.object(webapp, "search_tidal") as tidal_search:
            response = await webapp.api_middleware(request, webapp.search_handler)
        self.assertEqual(response.status, 400)
        tidal_search.assert_not_called()

    async def test_complete_resource_requires_confirmation(self) -> None:
        body = {
            "provider": "beatport",
            "url": "https://www.beatport.com/release/example/456",
            "title": "Example release",
        }
        request = FakeRequest(body, path="/api/download", app=self.app_state)
        response = await webapp.api_middleware(request, webapp.download_handler)
        self.assertEqual(response.status, 400)

        request = FakeRequest(
            {**body, "confirm_complete": True},
            path="/api/download",
            app=self.app_state,
        )
        response = await webapp.api_middleware(request, webapp.download_handler)
        self.assertEqual(response.status, 202)

    async def test_download_rejects_inactive_worker(self) -> None:
        musicdl.release_provider_lock(self.worker_handles.pop("tiddl"))
        request = FakeRequest(
            {"provider": "tiddl", "url": "track/123"},
            path="/api/download",
            app=self.app_state,
        )
        response = await webapp.api_middleware(request, webapp.download_handler)
        self.assertEqual(response.status, 503)
        self.assertIn("n'est pas démarré", response_json(response)["error"])

    async def test_status_contains_readable_recent_job(self) -> None:
        musicdl.enqueue(
            "tiddl",
            ["track/123"],
            metadata={"title": "Readable", "artist": "Artist", "kind": "track"},
            owner_id=TEST_USER_ID,
            ephemeral=True,
        )
        request = FakeRequest(
            method="GET", path="/api/status", app=self.app_state
        )
        response = await webapp.api_middleware(request, webapp.status_handler)
        payload = response_json(response)
        self.assertEqual(payload["recent"][0]["title"], "Readable")
        self.assertTrue(payload["active"]["tiddl"])

    async def test_admin_login_creates_a_local_session(self) -> None:
        request = FakeRequest(
            {"email": "admin", "password": "test-admin-password"},
            path="/api/admin/login",
            app=self.app_state,
        )
        with mock.patch.object(webapp, "ADMIN_PASSWORD", "test-admin-password"):
            response = await webapp.api_middleware(request, webapp.admin_login_handler)
        self.assertEqual(response.status, 200)
        token = response_json(response)["access_token"]
        self.assertIn(token, self.app_state[webapp.ADMIN_SESSIONS_KEY])

    async def test_signup_creates_pending_local_user(self) -> None:
        request = FakeRequest(
            {"email": "Test@Example.com", "password": "secret123"},
            path="/api/signup",
            app=self.app_state,
        )
        with mock.patch.object(webapp, "send_verification_code") as send_code:
            response = await webapp.api_middleware(request, webapp.signup_handler)
        self.assertEqual(response.status, 201)
        payload = response_json(response)
        self.assertTrue(payload["pending"])
        self.assertTrue(payload["needs_email_verification"])
        profiles = webapp.local_admin_profiles()
        self.assertEqual(profiles[0]["email"], "test@example.com")
        self.assertFalse(profiles[0]["approved"])
        self.assertFalse(profiles[0]["email_verified"])
        send_code.assert_called_once_with(profiles[0]["user_id"], "test@example.com")

    async def test_local_login_requires_email_verification_and_admin_approval(self) -> None:
        user = webapp.local_create_user("test@example.com", "secret123")
        request = FakeRequest(
            {"email": "test@example.com", "password": "secret123"},
            path="/api/login",
            app=self.app_state,
        )
        response = await webapp.api_middleware(request, webapp.login_handler)
        self.assertEqual(response.status, 403)
        self.assertIn("E-mail non vérifié", response_json(response)["error"])
        webapp.local_mark_email_verified(user["id"])
        response = await webapp.api_middleware(request, webapp.login_handler)
        self.assertEqual(response.status, 403)
        self.assertIn("en attente", response_json(response)["error"])
        profile = webapp.local_admin_profiles()[0]
        webapp.local_set_user_access(profile["user_id"], True)
        response = await webapp.api_middleware(request, webapp.login_handler)
        self.assertEqual(response.status, 200)
        self.assertIn("access_token", response_json(response))

    async def test_email_verify_consumes_code(self) -> None:
        user = webapp.local_create_user("test@example.com", "secret123")
        code = webapp.create_email_code(user["id"], "test@example.com", "verify_email")
        request = FakeRequest(
            {"email": "test@example.com", "code": code},
            path="/api/email/verify",
            app=self.app_state,
        )
        response = await webapp.api_middleware(request, webapp.email_verify_handler)
        self.assertEqual(response.status, 200)
        self.assertTrue(response_json(response)["verified"])
        self.assertTrue(webapp.local_admin_profiles()[0]["email_verified"])

    async def test_password_reset_code_updates_password(self) -> None:
        user = webapp.local_create_user("test@example.com", "secret123")
        code = webapp.create_email_code(user["id"], "test@example.com", "password_reset")
        request = FakeRequest(
            {"email": "test@example.com", "code": code, "password": "newsecret"},
            path="/api/password-reset/confirm",
            app=self.app_state,
        )
        response = await webapp.api_middleware(
            request, webapp.password_reset_confirm_handler
        )
        self.assertEqual(response.status, 200)
        self.assertIsNotNone(webapp.local_login_user("test@example.com", "newsecret"))
        self.assertTrue(webapp.local_admin_profiles()[0]["email_verified"])

    async def test_admin_routes_use_local_store(self) -> None:
        webapp.local_create_user("test@example.com", "secret123")
        request = FakeRequest(
            method="GET",
            path="/api/admin/profiles",
            headers={"Authorization": "Bearer admin-token"},
            app=self.app_state,
        )
        response = await webapp.api_middleware(
            request, webapp.admin_profiles_handler
        )
        self.assertEqual(response.status, 200)
        profile = response_json(response)["profiles"][0]
        self.assertFalse(profile["approved"])
        self.assertNotIn("email_confirmed", profile)

    async def test_admin_can_update_profile_approval(self) -> None:
        user = webapp.local_create_user("test@example.com", "secret123")
        request = FakeRequest(
            {"approved": True},
            method="PATCH",
            path=f"/api/admin/profiles/{user['id']}",
            headers={"Authorization": "Bearer admin-token"},
            app=self.app_state,
            match_info={"user_id": user["id"]},
        )
        response = await webapp.api_middleware(
            request, webapp.admin_profile_handler
        )
        self.assertEqual(response.status, 200)
        self.assertTrue(response_json(response)["profile"]["approved"])
        self.assertFalse(self.app_state[webapp.AUTH_CACHE_KEY])

    async def test_admin_can_update_user_password(self) -> None:
        user = webapp.local_create_user("test@example.com", "secret123")
        request = FakeRequest(
            {"password": "newsecret"},
            method="POST",
            path=f"/api/admin/profiles/{user['id']}/password",
            headers={"Authorization": "Bearer admin-token"},
            app=self.app_state,
            match_info={"user_id": user["id"]},
        )
        response = await webapp.api_middleware(
            request, webapp.admin_profile_password_handler
        )
        self.assertEqual(response.status, 200)
        self.assertTrue(response_json(response)["updated"])
        self.assertIsNotNone(webapp.local_login_user("test@example.com", "newsecret"))
        self.assertFalse(self.app_state[webapp.AUTH_CACHE_KEY])

    async def test_admin_stats_returns_daily_download_totals(self) -> None:
        user = webapp.local_create_user("test@example.com", "secret123")
        webapp.local_upsert_history(user["id"], [
            {
                "job_id": "job-1",
                "created_at": webapp.date.today().isoformat() + "T12:00:00Z",
                "state": "done",
                "track_count": 2,
                "estimated_savings_cents": 298,
            }
        ])
        request = FakeRequest(
            method="GET",
            path="/api/admin/stats",
            headers={"Authorization": "Bearer admin-token"},
            app=self.app_state,
        )
        response = await webapp.api_middleware(request, webapp.admin_stats_handler)
        self.assertEqual(response.status, 200)
        daily = response_json(response)["daily"]
        self.assertEqual(daily[-1]["tracks"], 2)
        self.assertEqual(daily[-1]["savings_cents"], 298)

    async def test_admin_can_delete_profile_auth_user(self) -> None:
        user = webapp.local_create_user("test@example.com", "secret123")
        request = FakeRequest(
            {},
            method="DELETE",
            path=f"/api/admin/profiles/{user['id']}",
            headers={"Authorization": "Bearer admin-token"},
            app=self.app_state,
            match_info={"user_id": user["id"]},
        )
        response = await webapp.api_middleware(
            request, webapp.admin_profile_delete_handler
        )
        self.assertEqual(response.status, 200)
        self.assertTrue(response_json(response)["deleted"])
        self.assertEqual(webapp.local_admin_profiles(), [])
        self.assertFalse(self.app_state[webapp.AUTH_CACHE_KEY])

    async def test_config_includes_public_url(self) -> None:
        with (
            mock.patch.object(webapp, "PUBLIC_URL", "https://tunesaver.galagain.com"),
        ):
            request = FakeRequest(method="GET", path="/api/config", app=self.app_state)
            response = await webapp.api_middleware(request, webapp.config_handler)
        self.assertEqual(response.status, 200)
        self.assertEqual(
            response_json(response)["public_url"],
            "https://tunesaver.galagain.com",
        )

    async def test_browser_delivery_is_private_and_kept_for_library(self) -> None:
        job_id = musicdl.enqueue(
            "tiddl",
            ["track/123"],
            owner_id=TEST_USER_ID,
            ephemeral=True,
        )
        pending = musicdl.QUEUE_ROOT / "pending" / "tiddl" / f"{job_id}.json"
        done = musicdl.QUEUE_ROOT / "done" / "tiddl" / f"{job_id}.json"
        job = musicdl.load_job(pending, expected_provider="tiddl")
        job_root = musicdl.DELIVERY_ROOT / TEST_USER_ID / job_id
        delivery_path = job_root / "TuneSaver-test.flac"
        delivery_path.parent.mkdir(parents=True)
        delivery_path.write_bytes(b"test-audio")
        job["delivery"] = {
            "name": delivery_path.name,
            "path": str(delivery_path.relative_to(musicdl.DELIVERY_ROOT)),
            "size": delivery_path.stat().st_size,
        }
        musicdl.atomic_write_json(done, job)
        pending.unlink()

        app = webapp.create_app()
        other_token = "other-user-token"
        app[webapp.AUTH_CACHE_KEY].update({
            hashlib.sha256(TEST_TOKEN.encode()).hexdigest(): (
                time.monotonic() + 3600,
                {"id": TEST_USER_ID, "email": "test@example.com"},
            ),
            hashlib.sha256(other_token.encode()).hexdigest(): (
                time.monotonic() + 3600,
                {
                    "id": "22222222-2222-4222-8222-222222222222",
                    "email": "other@example.com",
                },
            ),
        })
        async with TestClient(TestServer(app)) as client:
            denied = await client.post(
                "/api/download-ticket",
                json={"job_id": job_id},
                headers={"Authorization": f"Bearer {other_token}"},
            )
            self.assertEqual(denied.status, 404)

            ticket = await client.post(
                "/api/download-ticket",
                json={"job_id": job_id},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            self.assertEqual(ticket.status, 200)
            ticket_url = (await ticket.json())["url"]
            response = await client.get(ticket_url)
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), b"test-audio")

        self.assertTrue(job_root.exists())
        delivered = musicdl.load_job(done, expected_provider="tiddl")
        self.assertIn("delivery", delivered)
        self.assertFalse(delivered["delivery_deleted"])
        self.assertIn("delivered_at", delivered)

    async def test_collection_exports_are_complete_ordered_and_private(self) -> None:
        user = webapp.local_create_user("export@example.com", "secret123")
        other = webapp.local_create_user("otherexport@example.com", "secret123")
        playlist = webapp.local_create_playlist(user["id"], "Set du soir")
        empty = webapp.local_create_playlist(user["id"], "Vide")
        foreign = webapp.local_create_playlist(other["id"], "Privee")
        tag = webapp.local_create_tag(user["id"], "Set du soir")
        empty_tag = webapp.local_create_tag(user["id"], "Vide")
        foreign_tag = webapp.local_create_tag(other["id"], "Privee")
        links = ["https://listen.tidal.com/track/123", "https://www.beatport.com/track/test/456", "https://open.qobuz.com/track/789"]
        sources = [("done", url, user["id"]) for url in links] + [
            ("pending", links[0], user["id"]), ("failed", links[0], user["id"]),
            ("done", "", user["id"]), ("done", "javascript:alert(1)", user["id"]),
            ("done", links[0] + "\n" + links[1], user["id"]),
            ("done", "https://secret:password@example.com/track", user["id"]),
            ("done", links[0], other["id"]),
        ]
        with webapp.local_db() as db:
            for i, (state, url, owner) in enumerate(sources):
                job_id = f"export-{i}"
                db.execute("insert into download_history(job_id,user_id,provider,source_url,state,created_at) values (?,?,?,?,?,?)",
                           (job_id, owner, "tiddl", url, state, "2026-01-01"))
                db.execute("insert into playlist_items(playlist_id,job_id,created_at) values (?,?,?)", (playlist["id"], job_id, "2026-01-01"))
                db.execute("insert into tag_items(tag_id,job_id,created_at) values (?,?,?)", (tag["id"], job_id, "2026-01-01"))
            db.executemany("insert into download_history(job_id,user_id,provider,source_url,state,created_at) values (?,?,?,?,?,?)",
                           [(f"recent-{i}", user["id"], "tiddl", links[0], "done", "2026-09-08") for i in range(501)])
        app = webapp.create_app()
        app.cleanup_ctx.clear()
        app[webapp.AUTH_CACHE_KEY][hashlib.sha256(TEST_TOKEN.encode()).hexdigest()] = (
            time.monotonic() + 3600, {"id": user["id"], "email": user["email"]})
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        async with TestClient(TestServer(app)) as client:
            for kind, item, private, blank in (("playlists", playlist, foreign, empty), ("tags", tag, foreign_tag, empty_tag)):
                with self.subTest(kind=kind):
                    path = f"/api/{kind}/{item['id']}/export"
                    self.assertEqual((await client.get(path)).status, 401)
                    response = await client.get(path, headers=headers)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(await response.json(), {"name": "Set du soir", "links": links, "skipped": 7})
                    for collection_id in (private["id"], "missing"):
                        response = await client.get(f"/api/{kind}/{collection_id}/export", headers=headers)
                        self.assertEqual(response.status, 404)
                    response = await client.get(f"/api/{kind}/{blank['id']}/export", headers=headers)
                    self.assertEqual(await response.json(), {"name": "Vide", "links": [], "skipped": 0})

    async def test_playlist_folders_are_owned_persistent_and_keep_playlists_on_delete(self) -> None:
        user = webapp.local_create_user("folders@example.com", "secret123")
        other = webapp.local_create_user("otherfolders@example.com", "secret123")
        playlist = webapp.local_create_playlist(user["id"], "Jazz")
        foreign = webapp.local_create_playlist(other["id"], "Private")
        app = webapp.create_app()
        app.cleanup_ctx.clear()
        app[webapp.AUTH_CACHE_KEY][hashlib.sha256(TEST_TOKEN.encode()).hexdigest()] = (
            time.monotonic() + 3600, {"id": user["id"], "email": user["email"]})
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        async with TestClient(TestServer(app)) as client:
            for name in ("", "x" * 81, None):
                response = await client.post("/api/playlist-folders", json={"name": name}, headers=headers)
                self.assertEqual(response.status, 400)
            response = await client.post("/api/playlist-folders", json={"name": "Soirees"}, headers=headers)
            self.assertEqual(response.status, 201)
            folder = (await response.json())["id"]
            move_url = f"/api/playlists/{playlist['id']}/folder"
            response = await client.patch(move_url, json={"folder_id": folder}, headers=headers)
            self.assertEqual(response.status, 200)
            response = await client.patch(f"/api/playlists/{foreign['id']}/folder", json={"folder_id": folder}, headers=headers)
            self.assertEqual(response.status, 404)
            with webapp.local_db() as db:
                db.execute("insert into playlist_folders values (?, ?, ?, 0)", ("foreign", other["id"], "Private"))
            for method in (client.patch, client.delete):
                response = await method("/api/playlist-folders/foreign", json={"name": "No"}, headers=headers)
                self.assertEqual(response.status, 404)
            response = await client.patch(move_url, json={"folder_id": "foreign"}, headers=headers)
            self.assertEqual(response.status, 404)
            self.assertEqual(webapp.local_playlists(user["id"])[0]["folder_id"], folder)
            response = await client.patch(f"/api/playlist-folders/{folder}", json={"collapsed": True}, headers=headers)
            self.assertEqual(response.status, 200)
            response = await client.patch(f"/api/playlist-folders/{folder}", json={"name": "Chill"}, headers=headers)
            self.assertEqual(response.status, 200)
            response = await client.get("/api/library", headers=headers)
            library = await response.json()
            self.assertEqual(library["folders"], [{"id": folder, "name": "Chill", "collapsed": 1}])
            response = await client.patch(move_url, json={"folder_id": None}, headers=headers)
            self.assertEqual(response.status, 200)
            self.assertIsNone(webapp.local_playlists(user["id"])[0]["folder_id"])
            await client.patch(move_url, json={"folder_id": folder}, headers=headers)
            response = await client.delete(f"/api/playlist-folders/{folder}", json={}, headers=headers)
            self.assertEqual(response.status, 200)
            self.assertEqual(webapp.local_playlists(user["id"])[0]["id"], playlist["id"])
            self.assertIsNone(webapp.local_playlists(user["id"])[0]["folder_id"])

    async def test_playlist_import_is_owned_persistent_and_skips_wrong_matches(self) -> None:
        user = webapp.local_create_user("import@example.com", "secret123")
        app = webapp.create_app()
        app.cleanup_ctx.clear()
        app[webapp.AUTH_CACHE_KEY][hashlib.sha256(TEST_TOKEN.encode()).hexdigest()] = (
            time.monotonic() + 3600, {"id": user["id"], "email": user["email"]})
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        url = "https://open.spotify.com/playlist/3cEYpjA9oz9GiPac4AsH4n"
        async with TestClient(TestServer(app)) as client:
            rejected = await client.post("/api/playlist-imports", json={"url": "https://localhost/private"}, headers=headers)
            self.assertEqual(rejected.status, 400)
            response = await client.post("/api/playlist-imports", json={"url": url}, headers=headers)
            self.assertEqual(response.status, 202)
            result = await response.json()
            renamed = await client.patch(f"/api/playlists/{result['playlist_id']}",
                                         json={"name": "Mon import"}, headers=headers)
            self.assertEqual(renamed.status, 200)
            duplicate = await client.post("/api/playlist-imports", json={"url": url}, headers=headers)
            self.assertEqual((await duplicate.json())["id"], result["id"])
            self.assertEqual(webapp.local_imports(TEST_USER_ID), [])
            tracks = [{"title": "Song", "artist": "Artist", "duration": 180},
                      {"title": "Missing", "artist": "Artist", "duration": 180}]
            candidate = {"provider": "tiddl", "title": "Song", "artist": "Artist", "duration": "3:00",
                         "download_url": "https://listen.tidal.com/track/123"}
            with webapp.local_db() as db:
                record = dict(db.execute("select * from playlist_imports where id = ?", (result["id"],)).fetchone())
            with mock.patch.object(webapp, "read_import_source", return_value={"name": "Favorites", "tracks": tracks}), \
                 mock.patch.object(webapp, "worker_is_active", return_value=True), \
                 mock.patch.object(webapp, "search_tidal", return_value=[candidate]), \
                 mock.patch.object(webapp, "search_qobuz", return_value=[]), \
                 mock.patch.object(webapp, "search_beatport", return_value=[]):
                await webapp.process_playlist_import(app, record)
                status = webapp.local_imports(user["id"])[0]
                self.assertEqual(status["state"], "partial")
                self.assertEqual(status["queued"], 1)
                self.assertEqual(status["failures"][0]["title"], "Missing")
                playlist = webapp.local_playlists(user["id"])[0]
                self.assertEqual(playlist["name"], "Mon import")
                self.assertEqual(len(playlist["track_ids"]), 1)
                first_job = playlist["track_ids"][0]
                self.assertEqual(webapp.enqueue_import_track(record, candidate), first_job)
                with webapp.local_db() as db:
                    resumed = dict(db.execute("select * from playlist_imports where id = ?", (result["id"],)).fetchone())
                await webapp.process_playlist_import(app, resumed)
                self.assertEqual(webapp.local_imports(user["id"])[0]["queued"], 1)
            deleted = await client.delete(f"/api/playlists/{result['playlist_id']}", json={}, headers=headers)
            self.assertEqual(deleted.status, 200)
            self.assertEqual(webapp.local_imports(user["id"]), [])

    async def test_library_stream_and_playlists_use_owned_deliveries(self) -> None:
        user = webapp.local_create_user("library@example.com", "secret123")
        user_id = user["id"]
        job_id = musicdl.enqueue(
            "tiddl",
            ["track/123"],
            metadata={"title": "Library track", "artist": "Artist", "kind": "track"},
            owner_id=user_id,
            ephemeral=True,
        )
        pending = musicdl.QUEUE_ROOT / "pending" / "tiddl" / f"{job_id}.json"
        done = musicdl.QUEUE_ROOT / "done" / "tiddl" / f"{job_id}.json"
        job = musicdl.load_job(pending, expected_provider="tiddl")
        job_root = musicdl.DELIVERY_ROOT / user_id / job_id
        delivery_path = job_root / "TuneSaver-test.flac"
        delivery_path.parent.mkdir(parents=True)
        delivery_path.write_bytes(b"test-audio")
        cover_path = job_root / "cover.jpg"
        cover_path.write_bytes(b"fake-cover")
        job["delivery"] = {
            "name": delivery_path.name,
            "path": str(delivery_path.relative_to(musicdl.DELIVERY_ROOT)),
            "size": delivery_path.stat().st_size,
        }
        musicdl.atomic_write_json(done, job)
        pending.unlink()
        webapp.local_upsert_history(user_id, [{
            "job_id": job_id,
            "provider": "tiddl",
            "title": "Library track",
            "artist": "Artist",
            "resource_kind": "track",
            "state": "done",
            "created_at": job["created_at"],
        }])

        self.app_state[webapp.AUTH_CACHE_KEY] = {
            hashlib.sha256(TEST_TOKEN.encode()).hexdigest(): (
                time.monotonic() + 3600,
                {"id": user_id, "email": "library@example.com"},
            )
        }
        app = webapp.create_app()
        app[webapp.AUTH_CACHE_KEY].update(self.app_state[webapp.AUTH_CACHE_KEY])
        async with TestClient(TestServer(app)) as client:
            library = await client.get(
                "/api/library",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            self.assertEqual(library.status, 200)
            library_payload = await library.json()
            self.assertEqual(library_payload["tracks"][0]["job_id"], job_id)
            self.assertTrue(library_payload["tracks"][0]["streamable"])
            self.assertEqual(library_payload["tracks"][0]["cover_url"], f"/api/covers/{job_id}")

            mixer_url = f"/api/mixer/tracks/{job_id}"
            auth = {"Authorization": f"Bearer {TEST_TOKEN}"}
            private = await client.get(mixer_url)
            self.assertEqual(private.status, 401)
            analysis = await client.get(mixer_url, headers=auth)
            self.assertEqual(analysis.status, 200)
            saved = await client.patch(mixer_url, headers=auth, json={"bpm": 125, "hotcue": {"index": 0, "time": 2}})
            self.assertEqual(saved.status, 200)
            self.assertEqual((await saved.json())["settings"]["hotcues"][0], 2)
            reread = await client.get(mixer_url, headers=auth)
            self.assertEqual((await reread.json())["settings"]["bpm"], 125)
            deleted_cue = await client.patch(mixer_url, headers=auth, json={"hotcue": {"index": 0, "time": None}})
            self.assertIsNone((await deleted_cue.json())["settings"]["hotcues"][0])
            invalid = await client.patch(mixer_url, headers=auth, json={"bpm": 999})
            self.assertEqual(invalid.status, 400)
            foreign = webapp.local_create_user("foreign-mixer@example.com", "secret123")
            app[webapp.AUTH_CACHE_KEY][hashlib.sha256(b"foreign-mixer-token").hexdigest()] = (time.monotonic() + 3600, foreign)
            for method in ("GET", "PATCH", "POST"):
                denied = await client.request(method, mixer_url, headers={"Authorization": "Bearer foreign-mixer-token"}, json={"bpm": 128})
                self.assertEqual(denied.status, 404)
            restarted = await client.post(mixer_url, headers=auth, json={})
            self.assertEqual(restarted.status, 200)
            self.assertEqual((await restarted.json())["settings"]["bpm"], 125)

            playlist = await client.post(
                "/api/playlists",
                json={"name": "Favorites"},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            playlist_id = (await playlist.json())["playlist"]["id"]
            added = await client.post(
                f"/api/playlists/{playlist_id}/tracks",
                json={"job_id": job_id},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            self.assertEqual(added.status, 200)

            tag = await client.post(
                "/api/tags",
                json={"name": "Peak time"},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            self.assertEqual(tag.status, 201)
            tag_id = (await tag.json())["tag"]["id"]
            tagged = await client.post(
                f"/api/tags/{tag_id}/tracks",
                json={"job_id": job_id},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            self.assertEqual(tagged.status, 200)

            updated_library = await client.get(
                "/api/library",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            updated_payload = await updated_library.json()
            self.assertEqual(updated_payload["tags"][0]["track_ids"], [job_id])
            self.assertEqual(updated_payload["tracks"][0]["tags"][0]["name"], "Peak time")

            for collection, item_id in (("playlists", playlist_id), ("tags", tag_id)):
                renamed = await client.patch(
                    f"/api/{collection}/{item_id}", json={"name": "  Nouveau   nom  "},
                    headers={"Authorization": f"Bearer {TEST_TOKEN}"})
                self.assertEqual(renamed.status, 200)
                self.assertEqual((await renamed.json())["name"], "Nouveau nom")
                for name in ("  ", None, "x" * 81):
                    invalid = await client.patch(
                        f"/api/{collection}/{item_id}", json={"name": name},
                        headers={"Authorization": f"Bearer {TEST_TOKEN}"})
                    self.assertEqual(invalid.status, 400)
                with self.assertRaises(webapp.PublicError):
                    webapp.local_rename_collection(TEST_USER_ID, collection, item_id, "Interdit")
            self.assertEqual(webapp.local_playlists(user_id)[0]["track_ids"], [job_id])
            tags = webapp.local_tags(user_id)
            self.assertEqual(tags[0]["name"], "Nouveau nom")
            self.assertEqual(tags[0]["track_ids"], [job_id])
            self.assertEqual(tags[0]["color"], updated_payload["tags"][0]["color"])
            self.assertEqual(webapp.local_track_tag_map(user_id)[job_id][0]["name"], "Nouveau nom")

            cover = await client.get(
                f"/api/covers/{job_id}",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            self.assertEqual(cover.status, 200)
            self.assertEqual(await cover.read(), b"fake-cover")

            removed_tag = await client.delete(
                f"/api/tags/{tag_id}/tracks/{job_id}",
                json={},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            self.assertEqual(removed_tag.status, 200)

            ticket = await client.post(
                "/api/stream-ticket",
                json={"job_id": job_id},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            self.assertEqual(ticket.status, 200)
            stream_url = (await ticket.json())["url"]
            streamed = await client.get(stream_url)
            self.assertEqual(streamed.status, 200)
            self.assertEqual(await streamed.read(), b"test-audio")

            other = webapp.local_create_user("other-library@example.com", "secret123")
            with self.assertRaises(webapp.PublicError):
                webapp.local_library_remove_track(other["id"], job_id)
            self.assertEqual(len(webapp.local_library_tracks(user_id)), 1)
            webapp.local_tag_add_track(user_id, tag_id, job_id)
            removed = await client.delete(
                f"/api/library/{job_id}",
                json={},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            self.assertEqual(removed.status, 200)
            self.assertEqual(webapp.local_library_tracks(user_id), [])
            self.assertEqual(webapp.local_playlists(user_id)[0]["track_ids"], [])
            self.assertEqual(webapp.local_tags(user_id)[0]["track_ids"], [])
            self.assertEqual(webapp.local_user_history(user_id, limit=500, offset=0), [])
            self.assertFalse(delivery_path.exists())
            self.assertFalse(done.exists())
            expired = await client.get(stream_url)
            self.assertEqual(expired.status, 404)
            webapp.local_library_remove_track(user_id, job_id)

    async def test_delete_pending_failed_and_stale_history(self) -> None:
        user = webapp.local_create_user("delete@example.com", "secret123")
        other = webapp.local_create_user("keep@example.com", "secret123")
        owner = user["id"]
        for state in ("pending", "failed", "done"):
            with self.subTest(state=state):
                job = musicdl.enqueue("qobuz", ["https://play.qobuz.com/track/789"], owner_id=owner, ephemeral=True)
                pending = musicdl.QUEUE_ROOT / "pending" / "qobuz" / f"{job}.json"
                path = musicdl.QUEUE_ROOT / state / "qobuz" / pending.name
                pending.rename(path)
                content = musicdl.DELIVERY_ROOT / owner / job / "content"
                content.mkdir(parents=True)
                (content / "partial.flac").write_bytes(b"audio")
                log = musicdl.QUEUE_ROOT / "logs" / "qobuz" / f"{job}-attempt-1.log"
                log.write_text("private")
                stale = {"job_id": job, "provider": "qobuz", "state": state}
                if state == "done":
                    webapp.local_upsert_history(owner, [stale])
                    webapp.mixer_analysis.init_store(webapp.local_db)
                    with webapp.local_db() as db:
                        db.execute("insert into mixer_analysis(job_id,result) values(?,?)", (job, '{"bpm":128}'))
                        db.execute("insert into mixer_settings(job_id,user_id,settings) values(?,?,?)", (job, owner, '{"cue":12}'))
                with self.assertRaises(webapp.PublicError):
                    webapp.local_library_remove_track(other["id"], job)
                self.assertTrue(content.exists())
                webapp.local_library_remove_track(owner, job)
                self.assertFalse(path.exists())
                self.assertFalse(content.parent.exists())
                self.assertFalse(log.exists())
                webapp.local_upsert_history(owner, [stale])
                self.assertEqual(webapp.local_user_history(owner), [])
                self.assertEqual(webapp.queue_snapshot(owner)["recent"], [])
                self.assertFalse(webapp.update_existing_job(path, {"provider": "qobuz"}))
                self.assertFalse(path.exists())
                webapp.local_library_remove_track(owner, job)
                if state == "done":
                    with webapp.local_db() as db:
                        self.assertEqual(db.execute("select count(*) from mixer_analysis where job_id=?", (job,)).fetchone()[0], 0)
                        self.assertEqual(db.execute("select count(*) from mixer_settings where job_id=?", (job,)).fetchone()[0], 0)

    async def test_delete_active_download_cleans_partial_files(self) -> None:
        user = webapp.local_create_user("active-delete@example.com", "secret123")
        job = musicdl.enqueue("qobuz", ["https://play.qobuz.com/track/789"], owner_id=user["id"], ephemeral=True)
        pending = musicdl.QUEUE_ROOT / "pending" / "qobuz" / f"{job}.json"
        ready, resume = threading.Event(), threading.Event()
        errors = []

        def download(_provider, _urls, log, *, output_dir):
            (output_dir / "partial.flac").write_bytes(b"partial")
            log.write_text("partial download")
            ready.set()
            if not resume.wait(5):
                raise TimeoutError("test download not released")
            return 1

        def worker():
            try:
                musicdl.process_job("qobuz", pending)
            except Exception as exc:
                errors.append(exc)

        with mock.patch.object(musicdl, "run_provider", side_effect=download):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(ready.wait(3))
                webapp.local_library_remove_track(user["id"], job)
                self.assertEqual(webapp.queue_snapshot(user["id"])["recent"], [])
            finally:
                resume.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertFalse((musicdl.DELIVERY_ROOT / user["id"] / job).exists())
        self.assertEqual(list((musicdl.QUEUE_ROOT / "logs" / "qobuz").glob("*.log")), [])

    async def test_delete_does_not_follow_delivery_symlink(self) -> None:
        user = webapp.local_create_user("symlink-delete@example.com", "secret123")
        job = musicdl.enqueue("qobuz", ["https://play.qobuz.com/track/789"], owner_id=user["id"], ephemeral=True)
        outside = Path(self.temporary.name) / "keep"
        outside.mkdir()
        audio = outside / "keep.flac"
        audio.write_bytes(b"keep")
        owner_dir = musicdl.DELIVERY_ROOT / user["id"]
        owner_dir.mkdir()
        (owner_dir / job).symlink_to(outside, target_is_directory=True)
        webapp.local_library_remove_track(user["id"], job)
        self.assertEqual(audio.read_bytes(), b"keep")
        self.assertFalse((owner_dir / job).is_symlink())

    async def test_library_can_stream_single_audio_zip_and_read_cover(self) -> None:
        user = webapp.local_create_user("zip-library@example.com", "secret123")
        user_id = user["id"]
        job_id = musicdl.enqueue(
            "qobuz",
            ["https://play.qobuz.com/track/789"],
            metadata={"title": "Zip track", "artist": "Artist", "kind": "track"},
            owner_id=user_id,
            ephemeral=True,
        )
        pending = musicdl.QUEUE_ROOT / "pending" / "qobuz" / f"{job_id}.json"
        done = musicdl.QUEUE_ROOT / "done" / "qobuz" / f"{job_id}.json"
        job = musicdl.load_job(pending, expected_provider="qobuz")
        job_root = musicdl.DELIVERY_ROOT / user_id / job_id
        delivery_path = job_root / "TuneSaver-test.zip"
        delivery_path.parent.mkdir(parents=True)
        with zipfile.ZipFile(delivery_path, "w") as archive:
            archive.writestr("Album/track.m4a", b"zip-audio")
            archive.writestr("Album/cover.jpg", b"zip-cover")
        job["delivery"] = {
            "name": delivery_path.name,
            "path": str(delivery_path.relative_to(musicdl.DELIVERY_ROOT)),
            "size": delivery_path.stat().st_size,
        }
        musicdl.atomic_write_json(done, job)
        pending.unlink()
        webapp.local_upsert_history(user_id, [{
            "job_id": job_id,
            "provider": "qobuz",
            "title": "Zip track",
            "artist": "Artist",
            "resource_kind": "track",
            "state": "done",
            "created_at": job["created_at"],
        }])

        app = webapp.create_app()
        app[webapp.AUTH_CACHE_KEY] = {
            hashlib.sha256(TEST_TOKEN.encode()).hexdigest(): (
                time.monotonic() + 3600,
                {"id": user_id, "email": "zip-library@example.com"},
            )
        }
        async with TestClient(TestServer(app)) as client:
            library = await client.get(
                "/api/library",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            self.assertEqual(library.status, 200)
            payload = await library.json()
            self.assertTrue(payload["tracks"][0]["streamable"])
            self.assertEqual(payload["tracks"][0]["cover_url"], f"/api/covers/{job_id}")

            cover = await client.get(
                f"/api/covers/{job_id}",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            self.assertEqual(cover.status, 200)
            self.assertEqual(await cover.read(), b"zip-cover")

            ticket = await client.post(
                "/api/stream-ticket",
                json={"job_id": job_id},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            self.assertEqual(ticket.status, 200)
            streamed = await client.get((await ticket.json())["url"])
            self.assertEqual(streamed.status, 200)
            self.assertEqual(await streamed.read(), b"zip-audio")

    async def test_tidal_refresh_reuses_a_newer_token_from_disk(self) -> None:
        auth = {
            "token": "new-token",
            "refresh_token": "refresh",
            "user_id": "1",
            "country_code": "FR",
            "expires_at": int(webapp.time.time()) + 3600,
        }
        musicdl.atomic_write_json(webapp.TIDAL_AUTH_PATH, auth)
        with mock.patch.object(webapp.requests, "post") as post:
            result = webapp.refresh_tidal_auth(
                force=True, rejected_token="old-token"
            )
        self.assertEqual(result["token"], "new-token")
        post.assert_not_called()

    async def test_tidal_refresh_validates_and_persists_response(self) -> None:
        auth = {
            "token": "expired",
            "refresh_token": "refresh",
            "user_id": "1",
            "country_code": "FR",
            "expires_at": 1,
        }
        musicdl.atomic_write_json(webapp.TIDAL_AUTH_PATH, auth)
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "access_token": "fresh",
            "refresh_token": "rotated",
            "expires_in": 3600,
        }
        with (
            mock.patch(
                "tiddl.core.auth.client.get_auth_credentials",
                return_value=("client", "secret"),
            ),
            mock.patch.object(webapp.requests, "post", return_value=response),
        ):
            result = webapp.refresh_tidal_auth(force=False)
        persisted = json.loads(webapp.TIDAL_AUTH_PATH.read_text(encoding="utf-8"))
        self.assertEqual(result["token"], "fresh")
        self.assertEqual(persisted["refresh_token"], "rotated")
        self.assertEqual(webapp.TIDAL_AUTH_PATH.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
