from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "src" / "musicdl.py"
SPEC = importlib.util.spec_from_file_location("musicdl_module", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
musicdl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(musicdl)


class MusicDLTests(unittest.TestCase):
    def setUp(self) -> None:
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
        musicdl.CONFIG_ROOT.mkdir(parents=True)
        musicdl.SECRETS_ROOT.mkdir(parents=True)
        (musicdl.CONFIG_ROOT / "tiddl.toml").write_text(
            '[download]\ndownload_path = "/downloads/tiddl"\n', encoding="utf-8"
        )
        (musicdl.CONFIG_ROOT / "qobuz.ini").write_text(
            "[download]\ndefault_quality = 27\n", encoding="utf-8"
        )
        musicdl.STOP_EVENT.clear()
        musicdl.ensure_layout()

    def tearDown(self) -> None:
        for name, value in self.originals.items():
            setattr(musicdl, name, value)
        musicdl.STOP_EVENT.clear()
        self.temporary.cleanup()

    def test_url_validation(self) -> None:
        self.assertEqual(
            musicdl.validate_url("tiddl", "track/12345"),
            "track/12345",
        )
        self.assertEqual(
            musicdl.validate_url(
                "tiddl", "https://listen.tidal.com/browse/track/12345"
            ),
            "https://listen.tidal.com/browse/track/12345",
        )
        self.assertEqual(
            musicdl.validate_url(
                "beatport", "https://www.beatport.com/track/example/123"
            ),
            "https://www.beatport.com/track/example/123",
        )
        self.assertEqual(
            musicdl.validate_url(
                "beatport", "https://beatport.com/track/example/123"
            ),
            "https://www.beatport.com/track/example/123",
        )
        self.assertEqual(
            musicdl.validate_url("beatport", "https://api.beatport.com/v4/tracks/123"),
            "https://api.beatport.com/v4/tracks/123",
        )
        self.assertEqual(
            musicdl.validate_url("qobuz", "https://play.qobuz.com/track/123"),
            "https://play.qobuz.com/track/123",
        )
        self.assertEqual(
            musicdl.validate_url("qobuz", "https://www.qobuz.com/fr-fr/album/name/abc123"),
            "https://www.qobuz.com/fr-fr/album/name/abc123",
        )
        with self.assertRaises(ValueError):
            musicdl.validate_url("beatport", "https://example.com/track/123")
        with self.assertRaises(ValueError):
            musicdl.validate_url(
                "beatport", "https://links.beatport.com/track/example/123"
            )
        with self.assertRaises(ValueError):
            musicdl.validate_url("beatport", "https://www.beatport.com/")
        with self.assertRaises(ValueError):
            musicdl.validate_url("tiddl", "https://tidal.com/search?q=example")
        with self.assertRaises(ValueError):
            musicdl.validate_url("qobuz", "https://fake-qobuz.com/track/123")

    def test_enqueue_is_durable_and_deduplicates_urls(self) -> None:
        url = "https://tidal.com/album/123"
        job_id = musicdl.enqueue("tiddl", [url, url])
        path = musicdl.QUEUE_ROOT / "pending" / "tiddl" / f"{job_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["urls"], [url])
        self.assertEqual(payload["attempts"], 0)

    def test_beatport_config_keeps_secrets_ephemeral(self) -> None:
        (musicdl.CONFIG_ROOT / "beatport.json").write_text(
            '{"quality": "lossless"}\n', encoding="utf-8"
        )
        (musicdl.SECRETS_ROOT / "beatport_username").write_text(
            "user@example.test", encoding="utf-8"
        )
        (musicdl.SECRETS_ROOT / "beatport_password").write_text(
            'quote"and\\slash', encoding="utf-8"
        )
        runtime_dir = Path(self.temporary.name) / "runtime"
        runtime_dir.mkdir()
        config_path = musicdl.build_beatport_config(runtime_dir)
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["password"], 'quote"and\\slash')
        self.assertEqual(
            payload["downloads_directory"], str(musicdl.BEATPORT_DOWNLOAD_DIR)
        )
        self.assertTrue((runtime_dir / "beatportdl-err.log").is_file())

    def test_provider_commands_exit_after_the_batch(self) -> None:
        tiddl_url = "https://tidal.com/track/123"
        with mock.patch.object(musicdl, "stream_command", return_value=0) as stream:
            result = musicdl.run_provider(
                "tiddl", [tiddl_url], musicdl.QUEUE_ROOT / "tiddl.log"
            )
        self.assertEqual(result, 0)
        self.assertEqual(
            stream.call_args.args[0],
            ["tiddl", "download", "--raise-errors", "url", tiddl_url],
        )

        (musicdl.CONFIG_ROOT / "beatport.json").write_text("{}\n", encoding="utf-8")
        (musicdl.SECRETS_ROOT / "beatport_username").write_text("user", encoding="utf-8")
        (musicdl.SECRETS_ROOT / "beatport_password").write_text("pass", encoding="utf-8")
        beatport_url = "https://www.beatport.com/track/example/123"
        with mock.patch.object(musicdl, "stream_command", return_value=0) as stream:
            result = musicdl.run_provider(
                "beatport", [beatport_url], musicdl.QUEUE_ROOT / "beatport.log"
            )
        self.assertEqual(result, 0)
        self.assertEqual(stream.call_args.args[0], ["beatportdl", "-q", beatport_url])

        qobuz_values = {**musicdl.load_qobuz_settings(), **{
            "user_id": "123456",
            "user_auth_token": "token",
            "app_id": "123",
            "secrets": "secret",
            "musicdl_login_id": "login",
        }}
        musicdl.write_qobuz_config(qobuz_values)
        qobuz_url = "https://play.qobuz.com/track/456"
        with mock.patch.object(musicdl, "stream_command", return_value=0) as stream:
            result = musicdl.run_provider(
                "qobuz", [qobuz_url], musicdl.QUEUE_ROOT / "qobuz.log"
            )
        self.assertEqual(result, 0)
        self.assertEqual(
            stream.call_args.args[0],
            [
                musicdl.sys.executable,
                "-m",
                "qobuz_dl.cli",
                "dl",
                qobuz_url,
                "-d",
                str(musicdl.QOBUZ_DOWNLOAD_DIR),
                "-q",
                "27",
            ],
        )

    def test_qobuz_delivery_ignores_global_download_registry(self) -> None:
        musicdl.write_qobuz_config({**musicdl.load_qobuz_settings(),
            "user_id": "123456", "user_auth_token": "token", "app_id": "123",
            "secrets": "secret", "musicdl_login_id": "login"})
        for owner in ("first-user", "second-user"):
            output = musicdl.DELIVERY_ROOT / owner / "job" / "content"
            with mock.patch.object(musicdl, "stream_command", return_value=0) as stream:
                result = musicdl.run_provider("qobuz", ["https://play.qobuz.com/track/456"],
                                             musicdl.QUEUE_ROOT / "qobuz.log", output_dir=output)
            self.assertEqual(result, 0)
            command = stream.call_args.args[0]
            self.assertIn("--no-db", command)
            self.assertEqual(command[command.index("-d") + 1], str(output))
        self.assertEqual(musicdl.load_qobuz_settings()["no_database"], "false")

    def test_successful_job_moves_to_done(self) -> None:
        job_id = musicdl.enqueue("tiddl", ["https://tidal.com/track/123"])
        pending = musicdl.QUEUE_ROOT / "pending" / "tiddl" / f"{job_id}.json"
        with mock.patch.object(musicdl, "run_provider", return_value=0):
            self.assertTrue(musicdl.process_job("tiddl", pending))
        done = musicdl.QUEUE_ROOT / "done" / "tiddl" / f"{job_id}.json"
        payload = json.loads(done.read_text(encoding="utf-8"))
        self.assertEqual(payload["attempts"], 1)
        self.assertEqual(payload["last_exit_code"], 0)

    def test_ephemeral_job_is_isolated_and_prepares_browser_delivery(self) -> None:
        owner_id = "11111111-1111-4111-8111-111111111111"
        job_id = musicdl.enqueue(
            "tiddl",
            ["https://tidal.com/track/123"],
            owner_id=owner_id,
            ephemeral=True,
        )
        pending = musicdl.QUEUE_ROOT / "pending" / "tiddl" / f"{job_id}.json"

        def fake_download(_provider, _urls, _log, *, output_dir=None):
            assert output_dir is not None
            (output_dir / "track.flac").write_bytes(b"audio")
            return 0

        with mock.patch.object(musicdl, "run_provider", side_effect=fake_download):
            musicdl.process_job("tiddl", pending)

        done = musicdl.QUEUE_ROOT / "done" / "tiddl" / f"{job_id}.json"
        payload = json.loads(done.read_text(encoding="utf-8"))
        self.assertEqual(payload["owner_id"], owner_id)
        self.assertEqual(payload["track_count"], 1)
        self.assertEqual(payload["estimated_savings_cents"], 129)
        delivery = musicdl.DELIVERY_ROOT / payload["delivery"]["path"]
        self.assertEqual(delivery.read_bytes(), b"audio")

    def test_single_audio_with_cover_keeps_audio_as_delivery(self) -> None:
        owner_id = "11111111-1111-4111-8111-111111111111"
        job_id = musicdl.enqueue(
            "tiddl",
            ["https://tidal.com/track/123"],
            owner_id=owner_id,
            ephemeral=True,
        )
        pending = musicdl.QUEUE_ROOT / "pending" / "tiddl" / f"{job_id}.json"

        def fake_download(_provider, _urls, _log, *, output_dir=None):
            assert output_dir is not None
            (output_dir / "track.flac").write_bytes(b"audio")
            (output_dir / "cover.jpg").write_bytes(b"cover")
            return 0

        with mock.patch.object(musicdl, "run_provider", side_effect=fake_download):
            musicdl.process_job("tiddl", pending)

        done = musicdl.QUEUE_ROOT / "done" / "tiddl" / f"{job_id}.json"
        payload = json.loads(done.read_text(encoding="utf-8"))
        delivery = musicdl.DELIVERY_ROOT / payload["delivery"]["path"]
        self.assertEqual(delivery.name, "track.flac")
        self.assertEqual(delivery.read_bytes(), b"audio")
        self.assertTrue((delivery.parent / "cover.jpg").is_file())

    def test_ephemeral_job_requires_a_valid_owner(self) -> None:
        with self.assertRaises(ValueError):
            musicdl.enqueue(
                "tiddl",
                ["https://tidal.com/track/123"],
                owner_id="../../escape",
                ephemeral=True,
            )

    def test_failed_job_can_be_retried(self) -> None:
        job_id = musicdl.enqueue(
            "beatport", ["https://www.beatport.com/release/example/123"]
        )
        pending = musicdl.QUEUE_ROOT / "pending" / "beatport" / f"{job_id}.json"
        with mock.patch.object(musicdl, "run_provider", return_value=1):
            musicdl.process_job("beatport", pending)
        self.assertEqual(musicdl.retry_failed("beatport", job_id), 0)
        self.assertTrue(
            (musicdl.QUEUE_ROOT / "pending" / "beatport" / f"{job_id}.json").is_file()
        )

    def test_running_job_is_recovered_after_restart(self) -> None:
        job_id = musicdl.enqueue("tiddl", ["https://tidal.com/track/456"])
        pending = musicdl.QUEUE_ROOT / "pending" / "tiddl" / f"{job_id}.json"
        running = musicdl.QUEUE_ROOT / "running" / "tiddl" / pending.name
        pending.replace(running)
        self.assertEqual(musicdl.recover_running_jobs("tiddl"), 1)
        self.assertTrue(pending.is_file())

    def test_worker_lock_rejects_a_second_worker(self) -> None:
        with musicdl.provider_lock("tiddl", "worker", blocking=False):
            with self.assertRaises(RuntimeError):
                with musicdl.provider_lock("tiddl", "worker", blocking=False):
                    self.fail("le second verrou ne doit pas être acquis")

    def test_doctor_rejects_corrupt_tiddl_auth(self) -> None:
        auth_path = musicdl.STATE_ROOT / "tiddl" / "auth.json"
        auth_path.write_text("{}\n", encoding="utf-8")
        self.assertEqual(musicdl.doctor("tiddl"), 1)
        auth_path.write_text(
            json.dumps(
                {
                    "token": "token",
                    "refresh_token": "refresh",
                    "user_id": "123",
                    "country_code": "FR",
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(musicdl.doctor("tiddl"), 0)

    def test_poll_interval_must_be_positive_and_finite(self) -> None:
        for value in ("0", "-1", "nan", "inf"):
            with self.assertRaises(Exception):
                musicdl.positive_finite_float(value)


if __name__ == "__main__":
    unittest.main()
