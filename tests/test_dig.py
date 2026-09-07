import hashlib
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import dig
import dig_web
import webapp
import test_webapp as helpers


class RankingTests(unittest.TestCase):
    def test_identity_preserves_remixes_but_normalizes_original_mix(self):
        base = {"title": "Song", "artist": "Artist"}
        self.assertEqual(dig.identity(base), dig.identity({**base, "mix": "Original Mix"}))
        self.assertNotEqual(dig.identity(base), dig.identity({**base, "mix": "Club Remix"}))

    def test_repeated_collections_do_not_amplify_seeds(self):
        track = {"title": "Song", "artist": "Artist"}
        seeds = dig.select_seeds([([track] * 30, False), ([track], True)])
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["weight"], 2)

    def test_owned_rejected_and_duplicate_candidates_are_filtered(self):
        seed = dig.select_seeds([([{"title": "Seed", "artist": "A"}], True)])[0]
        track = {"title": "New", "artist": "B", "strength": 1, "engine": "ListenBrainz", "reason": "Similar"}
        ranked = dig.rank([(track, seed)] * 3, [seed], [], {})
        self.assertEqual(len(ranked), 1)
        self.assertEqual(len(ranked[0]["references"]), 1)
        self.assertEqual(dig.rank([(track, seed)], [seed], [track], {}), [])
        self.assertEqual(dig.rank([(track, seed)], [seed], [], {dig.identity(track): {"state": "rejected"}}), [])

    def test_single_ranking_uses_relevance_and_artist_diversity(self):
        seed = dig.select_seeds([([{"title": "Seed", "artist": "A"}], True)])[0]
        a = {"title": "New", "artist": "A", "strength": 1, "engine": "Test", "reason": "Similar"}
        b = {**a, "artist": "B", "strength": 0.9}
        c = {**a, "title": "Another"}
        self.assertEqual([t["artist"] for t in dig.rank([(a, seed), (b, seed), (c, seed)], [seed], [], {})], ["A", "B", "A"])


class DigHTTPTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = helpers.WebAppHTTPTests.asyncSetUp
    asyncTearDown = helpers.WebAppHTTPTests.asyncTearDown

    def fixture(self):
        user = webapp.local_create_user("dig@example.com", "secret123")
        app = webapp.create_app()
        app.cleanup_ctx.clear()
        app[webapp.AUTH_CACHE_KEY][hashlib.sha256(helpers.TEST_TOKEN.encode()).hexdigest()] = (
            time.monotonic() + 3600, {"id": user["id"], "email": user["email"]})
        return user, app, {"Authorization": f"Bearer {helpers.TEST_TOKEN}"}

    async def test_live_edits_reuse_workspace_and_invalidate_old_worker(self):
        user, app, headers = self.fixture()
        service = dig_web.DigService(webapp)
        tracks = [{"job_id": str(i), "title": f"Seed {i}", "artist": "A"} for i in range(2)]
        async with TestClient(TestServer(app)) as client, context_patch(tracks):
            self.assertIsNone((await (await client.get("/api/dig/live", headers=headers)).json())["dig"])
            response = await client.put("/api/dig/live", json={"references": [{"kind": "track", "id": "0"}], "revision": None}, headers=headers)
            first = (await response.json())["dig"]
            self.assertEqual(first["revision"], 0)
            self.assertNotIn("mode", first["config"])
            with webapp.local_db() as db:
                old = dict(db.execute("select * from dig_sessions where id = ?", (first["id"],)).fetchone())
            response = await client.put("/api/dig/live", json={"references": [{"kind": "track", "id": "1"}], "revision": 0}, headers=headers)
            second = (await response.json())["dig"]
            self.assertEqual(second["id"], first["id"])
            self.assertEqual(second["revision"], 1)
            self.assertEqual(second["seeds"][0]["title"], "Seed 1")
            service.save(first["id"], 0, state="done", results='[{"stale":true}]')
            with mock.patch.object(service.sources, "listenbrainz") as source:
                await service.process(app, old)
                source.assert_not_called()
            detail = (await (await client.get("/api/dig/live", headers=headers)).json())["dig"]
            self.assertEqual(detail["state"], "pending")
            self.assertEqual(detail["results"], [])
            stale = await client.put("/api/dig/live", json={"references": [], "revision": 0}, headers=headers)
            self.assertEqual(stale.status, 409)
            bad = await client.put("/api/dig/live", json={"references": [{"kind": "track", "id": "foreign"}], "revision": 1}, headers=headers)
            self.assertEqual(bad.status, 400)
            response = await client.put("/api/dig/live", json={"references": [], "revision": 1}, headers=headers)
            empty = (await response.json())["dig"]
            self.assertEqual(empty["state"], "idle")
            self.assertEqual(empty["config"]["references"], [])
            with webapp.local_db() as db:
                self.assertEqual(db.execute("select count(*) from dig_sessions where user_id = ?", (user["id"],)).fetchone()[0], 1)

    async def test_live_suggestion_reference_survives_result_replacement(self):
        _, app, headers = self.fixture()
        async with TestClient(TestServer(app)) as client, context_patch([{"job_id": "0", "title": "Seed", "artist": "A"}]):
            response = await client.put("/api/dig/live", json={"references": [{"kind": "track", "id": "0"}]}, headers=headers)
            first = (await response.json())["dig"]
            with webapp.local_db() as db:
                db.execute("update dig_sessions set results = ? where id = ?", (json.dumps([{"id": "new", "title": "New", "artist": "B"}]), first["id"]))
            ref = {"kind": "suggestion", "id": f"{first['id']}:new"}
            for revision in (0, 1):
                response = await client.put("/api/dig/live", json={"references": [ref], "revision": revision}, headers=headers)
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["dig"]["seeds"][0]["title"], "New")

    async def test_reference_ownership_and_mixed_selection(self):
        user, app, headers = self.fixture()
        tracks = [{"job_id": "owned", "title": "Seed", "artist": "A"}]
        playlist = webapp.local_create_playlist(user["id"], "Set")
        with webapp.local_db() as db:
            db.execute("insert into playlist_items values (?, ?, ?)", (playlist["id"], "owned", "now"))
        async with TestClient(TestServer(app)) as client, context_patch(tracks):
            script = await client.get("/dig.js")
            self.assertEqual(script.status, 200)
            bad = await client.post("/api/dig", json={"name": "X", "references": [{"kind": "track", "id": "foreign"}]}, headers=headers)
            self.assertEqual(bad.status, 400)
            response = await client.post("/api/dig", json={"name": "Test", "references": [
                {"kind": "track", "id": "owned"}, {"kind": "playlist", "id": playlist["id"]}]}, headers=headers)
            self.assertEqual(response.status, 202)
            session_id = (await response.json())["id"]
            detail = await (await client.get(f"/api/dig/{session_id}", headers=headers)).json()
            self.assertEqual(len(detail["seeds"]), 1)
            self.assertEqual(len(detail["config"]["references"]), 2)
            with webapp.local_db() as db:
                db.execute("update dig_sessions set user_id = ? where id = ?", (webapp.local_create_user("foreign@example.com", "secret123")["id"], session_id))
            for method in (client.get, client.patch, client.delete):
                response = await method(f"/api/dig/{session_id}", json={}, headers=headers)
                self.assertEqual(response.status, 404)

    async def test_worker_results_feedback_cancel_and_resume(self):
        user, app, headers = self.fixture()
        service = dig_web.DigService(webapp)
        seed = {"job_id": "owned", "title": "Seed", "artist": "A"}
        candidate = {"title": "New", "artist": "B", "engine": "ListenBrainz", "strength": 1, "reason": "Similar"}
        match = {"title": "New", "artist": "B", "provider": "tiddl", "id": "tiddl:track:42", "download_url": "https://tidal.com/track/42", "source_url": "https://tidal.com/track/42"}
        async with TestClient(TestServer(app)) as client, context_patch([seed]):
            response = await client.post("/api/dig", json={"name": "Test", "references": [{"kind": "track", "id": "owned"}]}, headers=headers)
            session_id = (await response.json())["id"]
            with webapp.local_db() as db:
                row = dict(db.execute("select * from dig_sessions where id = ?", (session_id,)).fetchone())
            with mock.patch.object(service.sources, "listenbrainz", return_value=[candidate]), mock.patch.object(service.sources, "lastfm", return_value=[]), mock.patch.object(service, "search", new=mock.AsyncMock(return_value=[match])):
                await service.process(app, row)
            detail = await (await client.get(f"/api/dig/{session_id}", headers=headers)).json()
            self.assertEqual(detail["state"], "done")
            self.assertEqual(detail["results"][0]["matches"][0]["provider"], "tiddl")
            item = detail["results"][0]["id"]
            response = await client.post("/api/dig", json={"name": "Deeper", "references": [{"kind": "suggestion", "id": f"{session_id}:{item}"}]}, headers=headers)
            self.assertEqual(response.status, 202)
            deeper_id = (await response.json())["id"]
            deeper = await (await client.get(f"/api/dig/{deeper_id}", headers=headers)).json()
            self.assertEqual(deeper["seeds"][0]["title"], "New")
            response = await client.post("/api/dig", json={"name": "Forbidden", "references": [{"kind": "suggestion", "id": f"foreign:{item}"}]}, headers=headers)
            self.assertEqual(response.status, 400)
            url = f"/api/dig/{session_id}/items/{item}"
            response = await client.post(url, json={"action": "rejected"}, headers=headers)
            self.assertEqual(response.status, 200)
            self.assertEqual(service.feedback(user["id"])[item]["state"], "rejected")
            response = await client.post(url, json={"action": "download", "provider": "qobuz"}, headers=headers)
            self.assertEqual(response.status, 400)
            response = await client.post(url, json={"action": "download", "provider": "tiddl", "playlist_id": "foreign"}, headers=headers)
            self.assertEqual(response.status, 400)
            response = await client.post(url, json={"action": "download", "provider": "tiddl"}, headers=headers)
            self.assertEqual(response.status, 202)
            job_id = (await response.json())["job_id"]
            playlist = webapp.local_create_playlist(user["id"], "Discoveries")
            response = await client.post(url, json={"action": "download", "provider": "tiddl", "playlist_id": playlist["id"]}, headers=headers)
            self.assertEqual(response.status, 202)
            self.assertEqual((await response.json())["job_id"], job_id)
            self.assertEqual(webapp.local_playlists(user["id"])[0]["track_ids"], [job_id])
            await client.patch(f"/api/dig/{session_id}", json={"action": "retry"}, headers=headers)
            await client.patch(f"/api/dig/{session_id}", json={"action": "cancel"}, headers=headers)
            service.save(session_id, state="done")
            detail = await (await client.get(f"/api/dig/{session_id}", headers=headers)).json()
            self.assertEqual(detail["state"], "cancelled")
            self.assertEqual(detail["results"][0]["feedback"], "rejected")

    async def test_active_session_limit_and_empty_reference(self):
        _, app, headers = self.fixture()
        async with TestClient(TestServer(app)) as client, context_patch([{"job_id": "owned", "title": "Seed", "artist": "A"}]):
            for i in range(3):
                response = await client.post("/api/dig", json={"name": "Test", "references": [{"kind": "track", "id": "owned"}]}, headers=headers)
                self.assertEqual(response.status, 202 if i < 2 else 429)

    async def test_cache_and_strict_recording_lookup(self):
        self.fixture()
        source = dig.Sources(webapp.local_db)
        response = mock.Mock(status_code=200)
        response.json.return_value = [{"recording_name": "Wrong", "artist_credit_name": "A", "recording_mbid": "id"}]
        with mock.patch.object(dig.requests, "get", return_value=response) as get:
            self.assertEqual(source.listenbrainz({"title": "Seed", "artist": "A"}), [])
            self.assertEqual(source.listenbrainz({"title": "Seed", "artist": "A"}), [])
            get.assert_called_once()


class context_patch:
    def __init__(self, tracks):
        self.patch = mock.patch.object(webapp, "local_library_tracks", return_value=tracks)

    async def __aenter__(self):
        self.patch.start()

    async def __aexit__(self, *args):
        self.patch.stop()
