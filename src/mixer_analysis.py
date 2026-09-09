"""Persistent per-owner mixer analysis and a single bounded background worker."""
import asyncio
import contextlib
import json
import logging
import math
import os
from pathlib import Path
import signal
import sys
import time

from aiohttp import web
from analyze_audio import KEYS, MAX_DURATION

LOGGER = logging.getLogger(__name__)


def init_store(db_factory):
    with db_factory() as db:
        db.executescript("""
          create table if not exists mixer_analysis (
            job_id text primary key references download_history(job_id) on delete cascade,
            status text not null default 'pending', signature text not null default '',
            result text, error text not null default '', updated real not null default 0
          );
          create table if not exists mixer_settings (
            job_id text primary key references download_history(job_id) on delete cascade,
            user_id text not null references users(id) on delete cascade,
            settings text not null default '{}'
          );
        """)
        if 'summary' not in {row[1] for row in db.execute('pragma table_info(mixer_analysis)')}:
            db.execute('alter table mixer_analysis add column summary text')
        db.execute("""update mixer_analysis set summary=json_object('bpm',json_extract(result,'$.bpm'),
          'key',json_extract(result,'$.key'),'duration',json_extract(result,'$.duration'),
          'grid_quality',json_extract(result,'$.grid_quality')) where result is not null and summary is null""")
        db.execute("""update mixer_analysis set status='pending',updated=0
          where status='done' and coalesce(json_extract(result,'$.version'),1)<2""")


def summaries(db_factory, owner):
    with db_factory() as db:
        rows = db.execute("""select h.job_id, a.status, a.error, s.settings, a.summary as result
          from download_history h left join mixer_analysis a on a.job_id=h.job_id
          left join mixer_settings s on s.job_id=h.job_id and s.user_id=h.user_id
          where h.user_id=? and h.state='done'""", (owner,)).fetchall()
    result = {}
    for row in rows:
        analysis = json.loads(row["result"] or "{}")
        settings = json.loads(row["settings"] or "{}")
        bpm = analysis.get("bpm") if settings.get("tempo_mode") == "variable" else settings.get("bpm", analysis.get("bpm"))
        key = settings.get("key", analysis.get("key"))
        result[row["job_id"]] = {"status": row["status"] or "pending", "bpm": bpm,
                                 "tempo_mode": settings.get("tempo_mode", "fixed"),
                                 "key": key, "camelot": KEYS.get(key), "duration": analysis.get("duration"),
                                 "grid_quality": analysis.get("grid_quality"), "error": row["error"] or ""}
    return result


class MixerAnalysis:
    def __init__(self, db_factory, delivery, public_error):
        self.db = db_factory
        self.delivery = delivery
        self.error = public_error
        self.wake = asyncio.Event()

    def owned(self, owner, job):
        with self.db() as db:
            row = db.execute("select 1 from download_history where job_id=? and user_id=? and state='done'", (job, owner)).fetchone()
        delivery = self.delivery(job, owner) if row else None
        if not delivery or not delivery["mime_type"]:
            raise self.error("Morceau introuvable.", status=404)
        return delivery

    def read(self, owner, job):
        self.owned(owner, job)
        with self.db() as db:
            row = db.execute("select * from mixer_analysis where job_id=?", (job,)).fetchone()
            settings = db.execute("select settings from mixer_settings where job_id=? and user_id=?", (job, owner)).fetchone()
        return {"status": row["status"] if row else "pending", "error": row["error"] if row else "",
                "analysis": json.loads(row["result"]) if row and row["result"] else None,
                "settings": json.loads(settings[0]) if settings else {}}

    def patch(self, owner, job, payload):
        data = self.read(owner, job)
        maximum = (data["analysis"] or {}).get("duration", MAX_DURATION)
        if not isinstance(payload, dict) or not payload or set(payload) - {"bpm", "key", "grid_offset", "cue", "hotcue", "tempo_mode"}:
            raise self.error("Réglages de mix invalides.")
        if "tempo_mode" in payload and payload["tempo_mode"] not in (None, "fixed", "variable"):
            raise self.error("Type d'analyse de tempo invalide.")

        def number(value, minimum, maximum):
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise self.error("Valeur de grille ou de cue invalide.")
            return value

        for name in ("bpm", "grid_offset", "cue"):
            if name in payload and payload[name] is not None:
                number(payload[name], 40 if name == "bpm" else 0, 300 if name == "bpm" else maximum)
        if "key" in payload and payload["key"] is not None and (not isinstance(payload["key"], str) or payload["key"] not in KEYS):
            raise self.error("Tonalité invalide.")
        hotcue = payload.get("hotcue")
        if "hotcue" in payload:
            if not isinstance(hotcue, dict) or set(hotcue) != {"index", "time"} or type(hotcue["index"]) is not int or not 0 <= hotcue["index"] < 8:
                raise self.error("Hot cue invalide.")
            if hotcue["time"] is not None:
                number(hotcue["time"], 0, maximum)
        with self.db() as db:
            db.execute("begin immediate")
            row = db.execute("select settings from mixer_settings where job_id=? and user_id=?", (job, owner)).fetchone()
            settings = json.loads(row[0]) if row else {}
            for name, value in payload.items():
                if name == "hotcue":
                    cues = settings.setdefault("hotcues", [None] * 8)
                    cues[value["index"]] = value["time"]
                elif value is None:
                    settings.pop(name, None)
                else:
                    settings[name] = value
            db.execute("insert into mixer_settings(job_id,user_id,settings) values(?,?,?) on conflict(job_id) do update set settings=excluded.settings where mixer_settings.user_id=excluded.user_id", (job, owner, json.dumps(settings)))
        return self.read(owner, job)

    async def handler(self, request):
        owner, job = request["user"]["id"], request.match_info["job_id"]
        if request.method == "PATCH":
            try:
                payload = await request.json()
            except (ValueError, TypeError):
                raise self.error("JSON invalide.")
            result = await asyncio.to_thread(self.patch, owner, job, payload)
        elif request.method == "POST":
            await asyncio.to_thread(self.owned, owner, job)
            with self.db() as db:
                db.execute("insert into mixer_analysis(job_id) values(?) on conflict(job_id) do update set status='pending',error='',updated=0 where mixer_analysis.status != 'running'", (job,))
            self.wake.set()
            result = await asyncio.to_thread(self.read, owner, job)
        else:
            result = await asyncio.to_thread(self.read, owner, job)
        return web.json_response(result)

    def next_job(self):
        with self.db() as db:
            db.execute("""insert or ignore into mixer_analysis(job_id)
              select h.job_id from download_history h
              where h.state='done' and not exists(select 1 from library_removed r where r.user_id=h.user_id and r.job_id=h.job_id)""")
            row = db.execute("""select a.job_id,h.user_id from mixer_analysis a join download_history h on h.job_id=a.job_id
              where a.status='pending' and h.state='done'
              and not exists(select 1 from library_removed r where r.user_id=h.user_id and r.job_id=h.job_id)
              order by a.updated,h.created_at desc limit 1""").fetchone()
            if row:
                db.execute("update mixer_analysis set status='running',updated=? where job_id=?", (time.time(), row["job_id"]))
        return dict(row) if row else None

    def finish(self, job, status, result=None, error="", signature=""):
        summary = {k: result.get(k) for k in ('bpm', 'key', 'duration', 'grid_quality')} if result else None
        with self.db() as db:
            db.execute("update mixer_analysis set status=?,result=?,summary=?,error=?,signature=?,updated=? where job_id=?", (status, json.dumps(result) if result else None, json.dumps(summary) if summary else None, error[:240], signature, time.time(), job))

    async def analyze_job(self, row):
        job = row["job_id"]
        process = None
        try:
            delivery = await asyncio.to_thread(self.owned, row["user_id"], job)
            path = delivery["path"]
            stat = path.stat()
            signature = f"{stat.st_size}:{stat.st_mtime_ns}"
            process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).with_name("analyze_audio.py")), str(path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
                env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "LANG": "C.UTF-8"})
            output, _ = await asyncio.wait_for(process.communicate(), timeout=300)
            result = json.loads(output)
            if process.returncode or result.get("error"):
                raise ValueError("Analyse impossible pour ce fichier. Réessayez ou renseignez la grille manuellement.")
            await asyncio.to_thread(self.owned, row["user_id"], job)
            if f"{path.stat().st_size}:{path.stat().st_mtime_ns}" != signature:
                raise ValueError("Le fichier a changé pendant l'analyse.")
            self.finish(job, "done", result=result, signature=signature)
        except asyncio.CancelledError:
            self.finish(job, "pending")
            raise
        except Exception as exc:
            self.finish(job, "failed", error=str(exc) if isinstance(exc, ValueError) else "Analyse interrompue ou fichier indisponible.")
        finally:
            if process:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()

    async def worker(self):
        while True:
            try:
                row = await asyncio.to_thread(self.next_job)
                if row:
                    await self.analyze_job(row)
                    await asyncio.sleep(1)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("mixer analysis worker")
            self.wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.wake.wait(), 15)

    async def context(self, _app):
        with self.db() as db:
            db.execute("update mixer_analysis set status='pending' where status='running'")
        task = asyncio.create_task(self.worker())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def register(app, db_factory, delivery, public_error):
    service = MixerAnalysis(db_factory, delivery, public_error)
    for method in ("GET", "PATCH", "POST"):
        app.router.add_route(method, "/api/mixer/tracks/{job_id}", service.handler)
    app.cleanup_ctx.append(service.context)
