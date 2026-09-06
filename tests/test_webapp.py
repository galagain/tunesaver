from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import time
import unittest
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
    ) -> None:
        self._payload = payload
        self.method = method
        self.path = path
        self.headers = {
            "Host": "127.0.0.1:8080",
            "Authorization": f"Bearer {TEST_TOKEN}",
            **(headers or {}),
        }
        self.content_type = "application/json" if method == "POST" else ""
        self.app = app or {}
        self._state = {"user": {"id": TEST_USER_ID, "email": "test@example.com"}}

    async def json(self, *, loads=json.loads):
        return self._payload

    def __getitem__(self, key):
        return self._state[key]

    def __setitem__(self, key, value):
        self._state[key] = value


def response_json(response) -> dict:
    return json.loads(response.text)


class WebAppFunctionTests(unittest.TestCase):
    def test_supabase_token_requires_an_approved_profile(self) -> None:
        user_response = mock.Mock(status_code=200)
        user_response.json.return_value = {
            "id": TEST_USER_ID,
            "email": "test@example.com",
        }
        profile_response = mock.Mock(status_code=200)
        profile_response.json.return_value = [{"approved": True}]
        with (
            mock.patch.object(webapp, "SUPABASE_URL", "https://project.supabase.co"),
            mock.patch.object(webapp, "SUPABASE_PUBLISHABLE_KEY", "publishable"),
            mock.patch.object(
                webapp.requests,
                "get",
                side_effect=[user_response, profile_response],
            ),
        ):
            user = webapp.verify_supabase_token("access-token")
        self.assertEqual(user["id"], TEST_USER_ID)
        self.assertTrue(user["approved"])

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
                        "album": {"title": "Album"},
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
                    "release": {"name": "Release"},
                    "key": {"name": "A Minor"},
                }
            ]
        }
        result = webapp.normalize_beatport_results(payload)[0]
        self.assertEqual(result["provider"], "beatport")
        self.assertEqual(result["artist"], "Producer")
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
                "album": {"title": "Album"},
            }]}
        }
        result = webapp.normalize_qobuz_results(payload)[0]
        self.assertEqual(result["provider"], "qobuz")
        self.assertEqual(result["artist"], "Artist")
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
        webapp.ASSET_ROOT = PROJECT_ROOT / "web"
        musicdl.ensure_layout()
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
        webapp.ASSET_ROOT = self.originals["ASSET_ROOT"]
        self.temporary.cleanup()

    async def test_index_has_security_headers(self) -> None:
        request = FakeRequest(method="GET", path="/", app=self.app_state)
        response = await webapp.api_middleware(request, webapp.index_handler)
        self.assertEqual(response.status, 200)
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        index = (PROJECT_ROOT / "web" / "index.html").read_text()
        script = (PROJECT_ROOT / "web" / "app.js").read_text()
        self.assertIn("TuneSaver", index)
        for provider in ("tiddl", "beatport", "qobuz"):
            self.assertIn(f'id="{provider}-health"', index)
        self.assertIn("setServiceStatus(provider", script)

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

    async def test_api_requires_a_supabase_session(self) -> None:
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

    async def test_browser_delivery_is_private_one_time_and_deleted(self) -> None:
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

        self.assertFalse(job_root.exists())
        delivered = musicdl.load_job(done, expected_provider="tiddl")
        self.assertNotIn("delivery", delivered)
        self.assertTrue(delivered["delivery_deleted"])
        self.assertIn("delivered_at", delivered)

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
