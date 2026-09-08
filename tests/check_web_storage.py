"""Run inside the deployed web container; only creates isolated test fixtures."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

sys.path.insert(0, "/app")
import musicdl
import webapp


with tempfile.TemporaryDirectory() as temporary:
    webapp.LOCAL_DB_PATH = Path(temporary) / "test.db"
    webapp.init_local_store()
    user = webapp.local_create_user("storage-test@example.invalid", "fixture-password")
    owner = user["id"]
    paths = []
    try:
        for provider in musicdl.PROVIDERS:
            for state in (*musicdl.STATES, "logs"):
                path = musicdl.QUEUE_ROOT / state / provider / f".storage-test-{uuid.uuid4().hex}"
                paths.append(path)
                path.write_text("fixture")
                path.unlink()
            job = f"20260908T000000Z-{uuid.uuid4().hex[:8]}"
            path = musicdl.QUEUE_ROOT / "failed" / provider / f"{job}.json"
            paths.append(path)
            url = {"tiddl": "https://tidal.com/track/123", "beatport": "https://www.beatport.com/track/test/123", "qobuz": "https://play.qobuz.com/track/123"}[provider]
            path.write_text(json.dumps({"id": job, "provider": provider, "owner_id": owner, "urls": [url], "ephemeral": True}))
            content = musicdl.DELIVERY_ROOT / owner / job / "content"
            content.mkdir(parents=True)
            (content / "partial.flac").write_bytes(b"fixture")
            log = musicdl.QUEUE_ROOT / "logs" / provider / f"{job}-attempt-1.log"
            paths.append(log)
            log.write_text("fixture")
            webapp.local_upsert_history(owner, [{"job_id": job, "provider": provider, "state": "failed"}])
            webapp.local_library_remove_track(owner, job)
            assert not path.exists() and not log.exists() and not content.parent.exists()
            assert not webapp.local_user_history(owner)
            print(f"{provider}: mounted queue writable; failed track, partial file and log deleted")
    finally:
        for path in paths:
            path.unlink(missing_ok=True)
        shutil.rmtree(musicdl.DELIVERY_ROOT / owner, ignore_errors=True)
