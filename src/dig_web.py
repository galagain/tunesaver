"""Owned, persistent discovery sessions and a bounded background worker."""

import asyncio
import contextlib
import json
import logging
import time
import uuid

from aiohttp import web

import dig
import playlist_sources


def register(app, host):
    service = DigService(host)
    service.initialize()
    app.cleanup_ctx.append(service.context)
    app.router.add_get("/api/dig", service.list_sessions)
    app.router.add_post("/api/dig", service.create)
    app.router.add_get("/api/dig/live", service.live_get)
    app.router.add_put("/api/dig/live", service.live_update)
    app.router.add_get("/api/dig/{dig_id}", service.get_session)
    app.router.add_patch("/api/dig/{dig_id}", service.update)
    app.router.add_delete("/api/dig/{dig_id}", service.delete)
    app.router.add_post("/api/dig/{dig_id}/items/{item_id}", service.item_action)


class DigService:
    def __init__(self, host):
        self.h = host
        self.sources = dig.Sources(host.local_db)

    def initialize(self):
        with self.h.local_db() as db:
            db.executescript("""
                create table if not exists dig_sessions (
                    id text primary key, user_id text not null references users(id) on delete cascade,
                    name text not null, config text not null, seeds text not null,
                    state text not null default 'pending', results text not null default '[]',
                    generated integer not null default 0, target integer not null default 20,
                    progress text not null default '', warnings text not null default '[]', created_at text not null
                );
                create index if not exists dig_sessions_user on dig_sessions(user_id, created_at);
                create table if not exists dig_feedback (
                    user_id text not null references users(id) on delete cascade, item_id text not null,
                    state text not null, artist text not null, primary key(user_id, item_id)
                );
                create table if not exists dig_cache (id text primary key, payload text not null, expires real not null);
                create table if not exists dig_live (
                    user_id text primary key references users(id) on delete cascade,
                    session_id text not null references dig_sessions(id) on delete cascade
                );
            """)
            if "revision" not in {r["name"] for r in db.execute("pragma table_info(dig_sessions)")}:
                db.execute("alter table dig_sessions add column revision integer not null default 0")

    def owned(self, request, db):
        row = db.execute("select * from dig_sessions where id = ? and user_id = ?",
                         (request.match_info["dig_id"], request["user"]["id"])).fetchone()
        if not row:
            raise self.h.PublicError("Recherche Dig introuvable.", status=404)
        return dict(row)

    def feedback(self, user_id):
        with self.h.local_db() as db:
            return {r["item_id"]: dict(r) for r in db.execute("select * from dig_feedback where user_id = ?", (user_id,))}

    async def list_sessions(self, request):
        with self.h.local_db() as db:
            rows = [dict(r) for r in db.execute(
                "select id, name, state, progress, created_at from dig_sessions where user_id = ? order by created_at desc",
                (request["user"]["id"],))]
        return web.json_response({"sessions": rows})

    async def resolve_references(self, user, refs, previous=None):
        if not isinstance(refs, list) or len(refs) > 100:
            raise self.h.PublicError("Choisissez au maximum 100 références.")
        tracks = await asyncio.to_thread(self.h.local_library_tracks, user)
        by_id = {t["job_id"]: t for t in tracks}
        playlists, tags = self.h.local_playlists(user), self.h.local_tags(user)
        with self.h.local_db() as db:
            folders = {r["id"]: dict(r) for r in db.execute("select id, name from playlist_folders where user_id = ?", (user,))}
        groups, clean_refs = [], []
        for ref in refs:
            if not isinstance(ref, dict) or not isinstance(ref.get("id"), str):
                raise self.h.PublicError("Référence invalide.")
            kind, key = ref.get("kind"), ref["id"]
            if kind == "track":
                item = by_id.get(key)
                ids = [key]
            elif kind in {"playlist", "tag"}:
                item = next((p for p in (playlists if kind == "playlist" else tags) if p["id"] == key), None)
                ids = item["track_ids"] if item else []
            elif kind == "folder":
                item = folders.get(key)
                ids = [i for p in playlists if p.get("folder_id") == key for i in p["track_ids"]]
            elif kind == "suggestion":
                dig_id, _, item_id = key.partition(":")
                with self.h.local_db() as db:
                    saved = db.execute("select results, target from dig_sessions where id = ? and user_id = ?", (dig_id, user)).fetchone()
                item = next((t for t in json.loads(saved["results"])[:saved["target"]] if t["id"] == item_id), None) if saved else None
                if item is None and previous:
                    item = next((r.get("seed") for r in json.loads(previous["config"]).get("references", [])
                                 if r["kind"] == "suggestion" and r["id"] == key), None)
                ids = []
            else:
                item = None
            if item is None:
                raise self.h.PublicError("Une référence n'est plus disponible dans votre bibliothèque.")
            groups.append(([item] if kind == "suggestion" else [by_id[i] for i in ids if i in by_id], kind in {"track", "suggestion"}))
            clean_refs.append({"kind": kind, "id": key, "name": item.get("name") or item.get("title")})
            if kind == "suggestion":
                clean_refs[-1]["seed"] = {k: item[k] for k in ("title", "artist", "mix") if k in item}
        seeds = dig.select_seeds(groups)
        if refs and not seeds:
            raise self.h.PublicError("Ces références ne contiennent aucun son disponible avec titre et artiste.")
        return seeds, clean_refs

    def serialize(self, row):
        row = dict(row)
        feedback = self.feedback(row["user_id"])
        results = json.loads(row.pop("results"))
        row["available"] = len(results)
        row["results"] = [{**t, "feedback": feedback.get(t["id"], {}).get("state", "")}
                          for t in results[:row["target"]]]
        for key in ("seeds", "config", "warnings"):
            row[key] = json.loads(row[key])
        row.pop("user_id")
        return row

    def live_row(self, user):
        with self.h.local_db() as db:
            row = db.execute("select s.* from dig_sessions s join dig_live l on s.id = l.session_id where l.user_id = ?", (user,)).fetchone()
        return dict(row) if row else None

    async def live_get(self, request):
        row = self.live_row(request["user"]["id"])
        return web.json_response({"dig": self.serialize(row) if row else None})

    async def live_update(self, request):
        payload = await self.h.read_json_object(request)
        user = request["user"]["id"]
        previous = self.live_row(user)
        expected = payload.get("revision")
        if expected != (previous["revision"] if previous else None):
            raise self.h.PublicError("Dig a changé dans un autre onglet.", status=409)
        seeds, refs = await self.resolve_references(user, payload.get("references"), previous)
        with self.h.local_db() as db:
            db.execute("begin immediate")
            latest = db.execute("select s.id, s.revision from dig_sessions s join dig_live l on s.id = l.session_id where l.user_id = ?", (user,)).fetchone()
            if (latest is None) != (previous is None) or (latest and (latest["revision"] != expected or latest["id"] != previous["id"])):
                raise self.h.PublicError("Dig a changé pendant la mise à jour.", status=409)
            session_id = previous["id"] if previous else str(uuid.uuid4())
            state = "pending" if seeds else "idle"
            config = json.dumps({"references": refs})
            if previous:
                db.execute("update dig_sessions set config = ?, seeds = ?, revision = revision + 1, state = ?, "
                           "results = '[]', generated = 0, target = 20, progress = '', warnings = '[]' where id = ?",
                           (config, json.dumps(seeds), state, session_id))
            else:
                db.execute("insert into dig_sessions (id,user_id,name,config,seeds,state,created_at) values (?,?,?,?,?,?,?)",
                           (session_id, user, "Dig", config, json.dumps(seeds), state, self.h.utc_timestamp()))
                db.execute("insert into dig_live values (?,?)", (user, session_id))
            # Old named searches must not compete with the live workspace.
            db.execute("update dig_sessions set state = 'cancelled' where user_id = ? and id != ? and state in ('pending','running')", (user, session_id))
        return web.json_response({"dig": self.serialize(self.live_row(user))})

    async def create(self, request):
        payload = await self.h.read_json_object(request)
        name, refs = payload.get("name", "Dig"), payload.get("references")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or "\x00" in name:
            raise self.h.PublicError("Nom invalide.")
        user = request["user"]["id"]
        seeds, clean_refs = await self.resolve_references(user, refs)
        if not seeds:
            raise self.h.PublicError("Ajoutez au moins une référence.")
        session_id = str(uuid.uuid4())
        with self.h.local_db() as db:
            db.execute("begin immediate")
            counts = db.execute("select count(*), sum(state in ('pending','running')) from dig_sessions where user_id = ?", (user,)).fetchone()
            if counts[0] >= 100 or (counts[1] or 0) >= 2:
                raise self.h.PublicError("Limite atteinte : 2 recherches actives et 100 recherches sauvegardées.", status=429)
            db.execute("insert into dig_sessions (id,user_id,name,config,seeds,created_at) values (?,?,?,?,?,?)",
                       (session_id, user, name.strip(), json.dumps({"references": clean_refs}),
                        json.dumps(seeds), self.h.utc_timestamp()))
        return web.json_response({"id": session_id}, status=202)

    async def get_session(self, request):
        with self.h.local_db() as db:
            row = self.owned(request, db)
        return web.json_response(self.serialize(row))

    async def update(self, request):
        payload = await self.h.read_json_object(request)
        with self.h.local_db() as db:
            row = self.owned(request, db)
            if "name" in payload:
                name = payload["name"]
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or "\x00" in name:
                    raise self.h.PublicError("Nom invalide.")
                db.execute("update dig_sessions set name = ? where id = ?", (name.strip(), row["id"]))
            elif payload.get("action") == "cancel":
                db.execute("update dig_sessions set state = 'cancelled', revision = revision + 1, progress = 'Dig arrêté' where id = ?", (row["id"],))
            elif payload.get("action") in {"more", "retry"}:
                if row["state"] in {"pending", "running"}:
                    raise self.h.PublicError("Cette recherche est déjà en cours.", status=409)
                count = db.execute("select count(*) from dig_sessions where user_id = ? and state in ('pending','running')", (row["user_id"],)).fetchone()[0]
                if count >= 2:
                    raise self.h.PublicError("Deux recherches sont déjà actives.", status=429)
                target = min(60, row["target"] + 20) if payload["action"] == "more" else row["target"]
                results = json.loads(row["results"])
                if payload["action"] == "retry":
                    for item in results:
                        if not item.get("matches"):
                            item["checked"] = False
                    if not results:
                        db.execute("update dig_sessions set generated = 0 where id = ?", (row["id"],))
                db.execute("update dig_sessions set state = 'pending', revision = revision + 1, target = ?, results = ? where id = ?",
                           (target, json.dumps(results), row["id"]))
            else:
                raise self.h.PublicError("Action inconnue.")
        return web.json_response({"updated": True})

    async def delete(self, request):
        with self.h.local_db() as db:
            row = self.owned(request, db)
            db.execute("delete from dig_sessions where id = ?", (row["id"],))
        return web.json_response({"deleted": True})

    async def item_action(self, request):
        payload = await self.h.read_json_object(request)
        with self.h.local_db() as db:
            row = self.owned(request, db)
        item = next((t for t in json.loads(row["results"])[:row["target"]] if t["id"] == request.match_info["item_id"]), None)
        if not item:
            raise self.h.PublicError("Suggestion introuvable.", status=404)
        action = payload.get("action")
        if action in {"kept", "rejected", "reset"}:
            with self.h.local_db() as db:
                db.execute("insert or replace into dig_feedback values (?,?,?,?)",
                           (row["user_id"], item["id"], "" if action == "reset" else action, item["artist"]))
            return web.json_response({"updated": True})
        if action != "download":
            raise self.h.PublicError("Action inconnue.")
        match = next((m for m in item["matches"] if m["provider"] == payload.get("provider")), None)
        if not match:
            raise self.h.PublicError("Aucune correspondance vérifiée sur cette source.")
        playlist_id = payload.get("playlist_id")
        if playlist_id is not None and not isinstance(playlist_id, str):
            raise self.h.PublicError("Playlist invalide.")
        job = await asyncio.to_thread(self.enqueue, row["user_id"], match, playlist_id)
        return web.json_response({"job_id": job}, status=202)

    def enqueue(self, user, match, playlist_id):
        h = self.h
        provider = match["provider"]
        if not h.worker_is_active(provider):
            raise h.PublicError("Cette source de téléchargement est arrêtée.", status=503)
        if playlist_id:
            return h.enqueue_import_track({"user_id": user, "playlist_id": playlist_id}, match)
        url = h.musicdl.validate_url(provider, match["download_url"])
        if h.resource_kind(provider, url) != "track":
            raise h.PublicError("La correspondance n'est pas un morceau.")
        with h.musicdl.provider_lock(provider, "enqueue"):
            job = h.existing_job(provider, url, user)
            if not job:
                for track in h.local_library_tracks(user):
                    if track.get("source_url") == url:
                        return track["job_id"]
                job = h.musicdl.enqueue(provider, [url], owner_id=user, ephemeral=True,
                                       metadata={"title": match["title"][:200], "artist": match["artist"][:200], "kind": "track"})
            return job

    def active(self, session_id, revision=0):
        with self.h.local_db() as db:
            return bool(db.execute("select 1 from dig_sessions where id = ? and revision = ? and state in ('pending','running')", (session_id, revision)).fetchone())

    def save(self, session_id, revision=0, **values):
        allowed = {"state", "progress", "results", "warnings", "generated"}
        assert values.keys() <= allowed
        with self.h.local_db() as db:
            db.execute("update dig_sessions set " + ",".join(f"{k} = ?" for k in values) +
                       " where id = ? and revision = ? and state in ('pending','running')", (*values.values(), session_id, revision))

    async def search(self, app, provider, query):
        h = self.h
        async with app[h.SEARCH_SEMAPHORES_KEY][provider]:
            return await asyncio.to_thread({"tiddl": h.search_tidal, "qobuz": h.search_qobuz, "beatport": h.search_beatport}[provider], query[:h.SEARCH_TEXT_LIMIT])

    async def process(self, app, row):
        h, session_id = self.h, row["id"]
        revision = row.get("revision", 0)
        def save(**values):
            self.save(session_id, revision, **values)
        seeds, results = json.loads(row["seeds"]), json.loads(row["results"])
        warnings = []
        providers = [p for p in ("tiddl", "beatport", "qobuz") if h.worker_is_active(p)]
        started = time.monotonic()
        if not self.active(session_id, revision):
            return
        save(state="running")
        if not row["generated"]:
            candidates = []
            for index, seed in enumerate(seeds):
                if not self.active(session_id, revision):
                    return
                if time.monotonic() - started > 600:
                    warnings.append("Temps de recherche atteint : certaines références n'ont pas été explorées.")
                    break
                save(progress=f"Références {index + 1}/{len(seeds)}")
                found = []
                for label, source in (("ListenBrainz", self.sources.listenbrainz), ("Last.fm", self.sources.lastfm)):
                    if not self.active(session_id, revision):
                        return
                    try:
                        found.extend(await asyncio.to_thread(source, seed))
                    except Exception:
                        warnings.append(f"{label} indisponible pour une référence.")
                if not found:
                    # Catalog fallback is explicitly labelled, not presented as sonic similarity.
                    for provider in providers:
                        if not self.active(session_id, revision):
                            return
                        if time.monotonic() - started > 600:
                            break
                        try:
                            tracks = await self.search(app, provider, seed["artist"])
                            found = [{**t, "engine": h.PLATFORM_NAMES[provider], "strength": 0.25,
                                      "reason": "Autre sortie du même artiste"} for t in tracks
                                     if dig.normalized_words(t.get("artist", "")) == dig.normalized_words(seed["artist"])]
                            if found:
                                break
                        except h.SourceError:
                            continue
                if not found:
                    warnings.append(f"Aucune suggestion pour {seed['artist']} · {seed['title']}.")
                candidates.extend((t, seed) for t in found)
            owned = await asyncio.to_thread(h.local_library_tracks, row["user_id"])
            results = dig.rank(candidates, seeds, owned, self.feedback(row["user_id"]))
            save(results=json.dumps(results), generated=1, warnings=json.dumps(sorted(set(warnings))))
        else:
            warnings = json.loads(row["warnings"])
        for index, item in enumerate(results[:row["target"]]):
            if not self.active(session_id, revision):
                return
            if time.monotonic() - started > 600:
                warnings.append("Vérification interrompue après 10 minutes. Relancez pour poursuivre.")
                break
            if item["checked"]:
                continue
            save(progress=f"Vérification des sources {index + 1}/{min(len(results), row['target'])}")
            if not item["matches"]:
                for provider in providers:
                    if not self.active(session_id, revision):
                        return
                    try:
                        tracks = await self.search(app, provider, f"{item['artist']} {item['title']}")
                        match = playlist_sources.matching_track(item, tracks)
                        if match:
                            item["matches"].append(match)
                            item["cover_url"] = match.get("cover_url") or item.get("cover_url", "")
                            break
                    except h.SourceError:
                        warnings.append(f"{h.PLATFORM_NAMES[provider]} temporairement indisponible.")
            else:
                item["cover_url"] = item["matches"][0].get("cover_url", "")
            item["checked"] = True
            save(results=json.dumps(results))
            await asyncio.sleep(0.25)
        if not providers:
            warnings.append("Aucune source de téléchargement active.")
        save(state="done", progress=f"{min(len(results), row['target'])} suggestions", warnings=json.dumps(sorted(set(warnings))))

    async def worker(self, app):
        while True:
            with self.h.local_db() as db:
                row = db.execute("select * from dig_sessions where state in ('pending','running') order by created_at limit 1").fetchone()
            if row:
                try:
                    await self.process(app, dict(row))
                except Exception as exc:
                    logging.getLogger(__name__).warning("Dig interrupted: %s", type(exc).__name__)
                    self.save(row["id"], row["revision"], state="failed", progress="Dig interrompu. Réessayez.")
            await asyncio.sleep(2)

    async def context(self, app):
        task = asyncio.create_task(self.worker(app))
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
