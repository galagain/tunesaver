import asyncio
import configparser
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import webapp


class ProviderProbeTests(unittest.TestCase):
    def test_tidal_subscription_and_auth_errors(self):
        auth = {"user_id": "fixture", "token": "secret"}
        cases = [
            (200, {"status": "ACTIVE", "subscription": {"type": "FREE"}}, "inactive"),
            (200, {"status": "ACTIVE", "subscription": {"type": "HIFI"}}, "active"),
            (200, {"status": "EXPIRED", "subscription": {"type": "HIFI"}}, "inactive"),
            (401, {}, "inactive"), (403, {}, "inactive"), (429, {}, "unknown"),
            (503, {}, "unknown"), (200, {}, "unknown"),
        ]
        for status, payload, expected in cases:
            with self.subTest(status=status, payload=payload), \
                 mock.patch.object(webapp, "refresh_tidal_auth", return_value=auth), \
                 mock.patch.object(webapp.requests, "get", return_value=mock.Mock(status_code=status, json=lambda: payload)) as get:
                value = webapp.probe_provider_connection("tiddl")
                self.assertEqual(value["state"], expected)
                self.assertNotIn("secret", str(value))
                self.assertEqual(get.call_args.kwargs["timeout"], (3, 8))

    def test_qobuz_entitlements_and_secret_safe_errors(self):
        config = configparser.ConfigParser()
        config["DEFAULT"] = {"user_id": "fixture", "user_auth_token": "do-not-leak", "app_id": "test"}
        with mock.patch.object(webapp.musicdl, "read_qobuz_config", return_value=config):
            for payload, expected in [
                ({"user": {"credential": {"parameters": {"short_label": "Studio"}}}}, "active"),
                ({"user": {"credential": {}}}, "inactive"), ({}, "unknown"),
            ]:
                with mock.patch.object(webapp.requests, "get", return_value=mock.Mock(status_code=200, json=lambda: payload)):
                    self.assertEqual(webapp.probe_provider_connection("qobuz")["state"], expected)
            with mock.patch.object(webapp.requests, "get", side_effect=webapp.requests.Timeout("url?user_auth_token=do-not-leak")):
                result = webapp.probe_provider_connection("qobuz")
                self.assertEqual(result["state"], "unknown")
                self.assertNotIn("do-not-leak", str(result))

    def test_beatport_never_assumes_missing_token_is_healthy(self):
        with mock.patch.object(webapp, "configured_beatport_proxy", return_value=""), \
             mock.patch.object(webapp, "valid_beatport_token", return_value=None):
            self.assertEqual(webapp.probe_provider_connection("beatport")["state"], "unknown")
        with mock.patch.object(webapp, "configured_beatport_proxy", return_value=""), \
             mock.patch.object(webapp, "valid_beatport_token", return_value="fixture"), \
             mock.patch.object(webapp, "beatport_api_search", return_value={}) as search:
            self.assertEqual(webapp.probe_provider_connection("beatport")["state"], "active")
            search.return_value = None
            self.assertEqual(webapp.probe_provider_connection("beatport")["state"], "inactive")


class ProviderStatusCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonblocking_shared_cache_expiry_and_worker_stop(self):
        app = {webapp.STATUS_CACHE_KEY: {}}
        workers = {p: True for p in webapp.musicdl.PROVIDERS}
        with mock.patch.object(webapp, "probe_provider_connection", return_value={"state": "active", "message": "Verified"}) as probe:
            first = await webapp.provider_statuses(app, workers)
            self.assertTrue(all(v["state"] == "loading" for v in first.values()))
            await webapp.provider_statuses(app, workers)
            await asyncio.gather(*list(app[webapp.STATUS_CACHE_KEY]["tasks"].values()))
            self.assertEqual(probe.call_count, 3)
            cached = await webapp.provider_statuses(app, workers)
            self.assertTrue(all(v["state"] == "active" for v in cached.values()))
            self.assertTrue(all("monotonic" not in v for v in cached.values()))
            self.assertEqual(probe.call_count, 3)
            stopped = await webapp.provider_statuses(app, {**workers, "tiddl": False})
            self.assertEqual(stopped["tiddl"]["state"], "inactive")
            self.assertFalse(stopped["tiddl"]["worker_running"])
            cache = app[webapp.STATUS_CACHE_KEY]["providers"]
            cache["qobuz"]["monotonic"] = time.monotonic() - webapp.PROVIDER_STATUS_SECONDS - 1
            probe.return_value = {"state": "inactive", "message": "No entitlement"}
            checking = await webapp.provider_statuses(app, workers)
            self.assertEqual(checking["qobuz"]["state"], "loading")
            await asyncio.gather(*list(app[webapp.STATUS_CACHE_KEY]["tasks"].values()))
            result = await webapp.provider_statuses(app, workers)
            self.assertEqual(result["qobuz"]["state"], "inactive")
            self.assertEqual(result["tiddl"]["state"], "inactive")
            self.assertEqual(result["beatport"]["state"], "active")


if __name__ == "__main__":
    unittest.main()
