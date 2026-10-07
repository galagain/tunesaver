#!/usr/bin/env python3
"""Private, metadata-only web control for run_matching_tests.py."""

import argparse
from collections import deque
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from export_playlist import ROOT
from run_matching_tests import CASES, case_id


class Controller:
    def __init__(self, source, output):
        self.source, self.output = Path(source).resolve(), Path(output).resolve()
        manifest = json.loads(self.source.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1:
            raise ValueError("Schema de metadonnees invalide.")
        indexed = {(c["source"], c["kind"]): c for c in manifest["collections"]}
        self.initial = {}
        for provider, kind, url in CASES:
            payload = indexed.get((provider, kind))
            identifier = case_id(provider, kind)
            self.initial[identifier] = {"id": identifier, "source": provider, "kind": kind,
                "name": payload["name"] if payload else identifier, "available": payload is not None,
                "status": "waiting", "provider": "", "error": "", "selected": False,
                "tracks": [{**t, "status": "waiting", "final": False, "error": "", "provider": "", "method": ""}
                           for t in payload["tracks"]] if payload else []}
        self.lock = threading.RLock()
        self.scenarios = copy.deepcopy(self.initial)
        self.logs = deque(maxlen=400)
        self.process = None
        self.status = "idle"
        self.revision = 0
        self.started = None
        self.ended = None
        self.directory = None
        self.job = None
        self.exit_code = None
        self.max_tracks = 0

    def start(self, selected, max_tracks=0):
        if type(max_tracks) is not int or max_tracks < 0:
            raise ValueError("La limite doit etre un entier positif ou 0 (tous).")
        if not isinstance(selected, list) or not selected or any(not isinstance(x, str) for x in selected):
            raise ValueError("Selectionnez au moins un scenario.")
        if len(set(selected)) != len(selected) or any(x not in self.initial or not self.initial[x]["available"] for x in selected):
            raise ValueError("Selection de scenarios invalide.")
        with self.lock:
            if self.status in {"running", "stopping", "cleanup_error"}:
                raise RuntimeError("Un appariement est deja en cours.")
            self.scenarios = copy.deepcopy(self.initial)
            self.max_tracks = max_tracks
            for identifier in selected:
                self.scenarios[identifier]["selected"] = True
                self.scenarios[identifier]["status"] = "queued"
                scenario = self.scenarios[identifier]
                scenario["source_total"] = len(scenario["tracks"])
                if max_tracks and scenario["kind"] in {"playlist", "album"}:
                    scenario["tracks"] = scenario["tracks"][:max_tracks]
            self.logs.clear()
            self.output.mkdir(parents=True, exist_ok=True)
            self.job = secrets.token_hex(16)
            command = [sys.executable, "-u", str(ROOT / "run_matching_tests.py"), "--docker", "--events",
                       "--input", str(self.source), "--output-dir", str(self.output)]
            command += ["--max-tracks", str(max_tracks)]
            for identifier in selected:
                command += ["--case", identifier]
            self.process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True,
                env={**os.environ, "TUNESAVER_MATCHING_JOB": self.job})
            self.status, self.started, self.ended, self.directory, self.exit_code = "running", time.time(), None, None, None
            self.revision += 1
            threading.Thread(target=self.consume, args=(self.process,), daemon=True).start()

    def event(self, event):
        with self.lock:
            kind = event.get("type")
            if kind == "run_started":
                directory = Path(event["directory"]).resolve()
                if directory.is_relative_to(self.output):
                    self.directory = directory
            scenario = self.scenarios.get(event.get("case"))
            if scenario:
                if kind == "case_started":
                    scenario["status"] = "running"
                elif kind in {"provider", "album_started"}:
                    scenario["provider"] = event["provider"]
                elif kind == "album_updated" and event.get("error"):
                    self.logs.append({"time": time.time(), "message": f"[{scenario['id']}] Album : {event['error']}", "level": "error"})
                elif kind in {"track_started", "track_updated"}:
                    number = event.get("number")
                    if type(number) is int and 1 <= number <= len(scenario["tracks"]):
                        track = scenario["tracks"][number - 1]
                        if kind == "track_started":
                            track.update(status="searching", provider=event["provider"])
                        else:
                            track.update({key: event[key] for key in ("status", "final", "target", "error", "method") if key in event})
                            if track.get("error"):
                                message = f"[{scenario['id']} / {number}] {track['title']} : {track['error']}"
                                if not self.logs or self.logs[-1]["message"] != message:
                                    self.logs.append({"time": time.time(), "message": message, "level": "error"})
                elif kind == "case_finished":
                    result = event["result"]
                    scenario["status"] = "complete" if result.get("complete") else "error" if result["status"] == "failed" else "partial"
                    scenario["error"] = result.get("error", "")
            self.revision += 1

    def consume(self, process):
        try:
            for line in process.stdout:
                line = line.strip()
                if line.startswith("TUNESAVER_EVENT:"):
                    try:
                        self.event(json.loads(line.removeprefix("TUNESAVER_EVENT:")))
                    except (ValueError, KeyError, TypeError):
                        with self.lock:
                            self.logs.append({"time": time.time(), "level": "error", "message": "Evenement de progression invalide."})
                elif line:
                    with self.lock:
                        self.logs.append({"time": time.time(), "message": line[:2000], "level": "error" if "ECHEC" in line or "Erreur :" in line else "info"})
                        self.revision += 1
        finally:
            code = process.wait()
            with self.lock:
                self.exit_code = code
                if self.status not in {"stopping", "cancelled", "cleanup_error"}:
                    self.status = "complete" if code == 0 else "partial" if code == 1 else "error"
                    self.ended = time.time()
                    for scenario in self.scenarios.values():
                        if scenario["status"] in {"running", "queued"}:
                            scenario.update(status="error", error="Le script s'est interrompu avant la fin du scenario.")
                        for track in scenario["tracks"]:
                            if track["status"] == "searching":
                                track.update(status="error", error="Recherche interrompue.")
                self.revision += 1

    def stop(self):
        with self.lock:
            if self.status not in {"running", "stopping"}:
                raise RuntimeError("Aucun appariement en cours.")
            if self.status == "stopping":
                return
            self.status = "stopping"
            self.revision += 1
            threading.Thread(target=self.cancel, args=(self.process, self.job), daemon=True).start()

    def cancel(self, process, job):
        try:
            try:
                os.killpg(process.pid, signal.SIGINT)
                process.wait(timeout=25)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            found = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=tunesaver.matching-job={job}"],
                                   capture_output=True, text=True, timeout=15, check=True)
            ids = [cid for cid in found.stdout.split() if re.fullmatch(r"[a-f0-9]{12,64}", cid)]
            if ids:
                subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, timeout=20, check=True)
            with self.lock:
                for scenario in self.scenarios.values():
                    if scenario["status"] in {"running", "queued"}:
                        scenario["status"] = "cancelled"
                    for track in scenario["tracks"]:
                        if track["status"] == "searching":
                            track["status"] = "cancelled"
                self.status = "cancelled"
        except (OSError, subprocess.SubprocessError):
            with self.lock:
                self.status = "cleanup_error"
                self.logs.append({"time": time.time(), "level": "error", "message": "Arret incomplet : verifier les conteneurs Docker avant de relancer."})
        finally:
            with self.lock:
                self.ended = time.time()
                self.revision += 1

    def snapshot(self):
        with self.lock:
            scenarios = []
            for scenario in self.scenarios.values():
                summary = {k: v for k, v in scenario.items() if k != "tracks"}
                summary.update(total=len(scenario["tracks"]), done=sum(t["final"] for t in scenario["tracks"]),
                               matched=sum(t["final"] and t["status"] == "matched" for t in scenario["tracks"]),
                               errors=sum(bool(t["error"]) for t in scenario["tracks"]))
                scenarios.append(summary)
            selected = [s for s in scenarios if s["selected"]]
            artifacts = [name for name in ("viewer.html", "inputs.json", "report.json") if self.directory and (self.directory / name).is_file()]
            return {"revision": self.revision, "status": self.status, "started": self.started, "ended": self.ended,
                    "exit_code": self.exit_code, "max_tracks": self.max_tracks, "scenarios": scenarios, "logs": list(self.logs), "artifacts": artifacts,
                    "total": sum(s["total"] for s in selected), "done": sum(s["done"] for s in selected),
                    "matched": sum(s["matched"] for s in selected), "errors": sum(s["errors"] for s in selected)}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass

    def send(self, body, code=200, content_type="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        for key, value in {"Content-Type": content_type, "Content-Length": str(len(body)), "Cache-Control": "no-store",
                           "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}.items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self, api=False):
        host = self.headers.get("Host", "")
        if host not in self.server.hosts or self.headers.get("Origin") not in (None, f"http://{host}"):
            self.send({"error": "Origine refusee."}, 403)
            return False
        if api and not secrets.compare_digest(self.headers.get("X-Matching-Token", ""), self.server.token):
            self.send({"error": "Session invalide. Rechargez la page."}, 403)
            return False
        return True

    def do_GET(self):
        path = urlparse(self.path)
        if not self.authorized(api=path.path.startswith("/api/")):
            return
        controller = self.server.controller
        if path.path == "/api/state":
            self.send(controller.snapshot())
        elif path.path == "/api/scenario":
            key = parse_qs(path.query).get("id", [""])[0]
            with controller.lock:
                scenario = copy.deepcopy(controller.scenarios.get(key))
            self.send(scenario or {"error": "Scenario inconnu."}, 200 if scenario else 404)
        elif path.path == "/api/artifact":
            name = parse_qs(path.query).get("name", [""])[0]
            with controller.lock:
                directory = controller.directory
            if name not in {"viewer.html", "inputs.json", "report.json"} or not directory or not (directory / name).is_file():
                self.send({"error": "Resultat indisponible."}, 404)
            else:
                self.send((directory / name).read_bytes(), content_type="text/html; charset=utf-8" if name.endswith("html") else "application/json")
        else:
            files = {"/": ("web/matching-live/index.html", "text/html; charset=utf-8"),
                     "/app.js": ("web/matching-live/app.js", "text/javascript"),
                     "/style.css": ("web/matching-live/style.css", "text/css"),
                     "/base.css": ("web/test-viewer/style.css", "text/css"),
                     "/icons.js": ("web/vendor/player-icons.js", "text/javascript"),
                     "/controls.js": ("web/vendor/mixer-icons.js", "text/javascript")}
            if path.path not in files:
                self.send({"error": "Introuvable."}, 404)
                return
            file, mime = files[path.path]
            body = (ROOT / file).read_bytes()
            if path.path == "/":
                body = body.replace(b"MATCHING_TOKEN", self.server.token.encode())
            self.send(body, content_type=mime)

    def do_POST(self):
        if not self.authorized(api=True):
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096 or self.headers.get_content_type() != "application/json":
                raise ValueError("Requete JSON invalide.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Requete invalide.")
            if self.path == "/api/start":
                self.server.controller.start(data.get("cases"), data.get("max_tracks", 0))
            elif self.path == "/api/stop":
                self.server.controller.stop()
            else:
                self.send({"error": "Introuvable."}, 404)
                return
            self.send(self.server.controller.snapshot())
        except (ValueError, RuntimeError) as exc:
            self.send({"error": str(exc)}, 409 if isinstance(exc, RuntimeError) else 400)
        except OSError:
            self.send({"error": "Impossible de lancer le script."}, 500)


def make_server(host, port, controller):
    server = ThreadingHTTPServer((host, port), Handler)
    server.controller, server.token = controller, secrets.token_urlsafe(32)
    actual_port = server.server_address[1]
    server.hosts = {f"{host}:{actual_port}", f"localhost:{actual_port}", f"127.0.0.1:{actual_port}"}
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8094)
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    address = ipaddress.ip_address(args.host)
    if address.version != 4 or address.is_unspecified or not (address.is_private or address in ipaddress.ip_network("100.64.0.0/10")):
        parser.error("Choisissez une adresse IPv4 locale ou Tailscale, pas une interface publique.")
    source = args.input
    if source is None:
        sources = sorted((ROOT / "data/playlist-tests").glob("*/inputs.json"), key=lambda p: p.stat().st_mtime)
        if not sources:
            parser.error("Aucun inputs.json disponible.")
        source = sources[-1]
    controller = Controller(source, source.parent / "matching-live")
    server = make_server(args.host, args.port, controller)
    print(f"Visualisation : http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if controller.status == "running":
            controller.stop()
        while controller.status == "stopping":
            time.sleep(.1)
    finally:
        server.server_close()


if __name__ == "__main__":
    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    main()
