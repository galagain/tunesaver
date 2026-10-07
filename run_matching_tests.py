#!/usr/bin/env python3
"""Match the metadata test scenarios without downloading audio or rereading sources."""

import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

import export_playlist
from run_playlist_tests import CASES, validate

EVENTS = False
PROGRESS = False


def track_limit(value):
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("La limite doit etre un entier positif ou 0.")
    if number < 0:
        raise argparse.ArgumentTypeError("La limite doit etre positive ou 0 (tous).")
    return number


def emit(event):
    if EVENTS:
        print("TUNESAVER_EVENT:" + json.dumps(event, ensure_ascii=True), file=sys.stderr, flush=True)


def case_id(source, kind):
    return source if kind == "playlist" else f"{source}-{kind}"


def match_payload(payload, catalog):
    from catalog_matcher import direct_target, match_collection
    validate(payload, payload["source"], payload["kind"])
    tracks = []
    for original in payload["tracks"]:
        track = {**original, "duration": original["duration_seconds"], "source_url": original["url"]}
        if original["target"]:
            target = original["target"]
            track.update(provider=target["provider"], source_url=target["url"])
            if direct_target(track) != target:
                raise ValueError("Cible native invalide dans le fichier source.")
        tracks.append(track)
    collection = {"name": payload["name"], "source": payload["url"], "kind": payload["kind"], "tracks": tracks}
    limit = payload.get("_max_tracks", 0) if payload["kind"] in {"playlist", "album"} else 0
    total = min(len(tracks), limit) if limit else len(tracks)
    bar = None
    if PROGRESS and not EVENTS:
        from tqdm import tqdm
        bar = tqdm(total=total, desc=case_id(payload["source"], payload["kind"]), unit="son", file=sys.stderr, dynamic_ncols=True)
    completed = set()

    def on_event(event):
        emit({**event, "case": case_id(payload["source"], payload["kind"])})
        if bar is not None:
            if event["type"] == "provider":
                bar.set_postfix_str(event["provider"])
            if event["type"] == "track_updated" and event.get("final") and event["number"] not in completed:
                completed.add(event["number"])
                bar.update(1)

    try:
        result = match_collection(collection, catalog,
            progress=None if bar is not None else lambda message: print(message, file=sys.stderr, flush=True),
            on_event=on_event, max_tracks=limit)
    finally:
        if bar is not None:
            bar.close()
    output = []
    for original, row in zip(payload["tracks"], result["tracks"]):
        match = row["match"]
        target = None
        if match:
            target = direct_target(match)
            if not target:
                raise ValueError("Le candidat apparie ne contient pas de lien natif valide.")
            target["method"] = match["method"]
        output.append({**original, "target": target, "requires_matching": target is None,
                       "matching": {"status": row["status"], "candidate": match,
                                    "attempts": row["attempts"]}})
    summary = {status: sum(t["matching"]["status"] == status for t in output)
               for status in ("matched", "not_found", "ambiguous", "unavailable", "error")}
    summary.update({method: sum(t["target"] is not None and t["target"]["method"] == method for t in output)
                    for method in ("direct", "album", "track")})
    return {**{k: v for k, v in payload.items() if k != "_max_tracks"}, "tracks": output, "total": len(output),
            "source_total": payload["total"], "max_tracks": limit,
            "matching": {"summary": summary, "album_attempts": result["album_attempts"]}}


def docker_match(payload, image, container):
    with tempfile.TemporaryDirectory(prefix="matching-container-") as temp:
        cidfile = Path(temp) / "cid"
        command = ["docker", "run", "--rm", "-i", "--cidfile", str(cidfile), "--volumes-from", container]
        job = os.environ.get("TUNESAVER_MATCHING_JOB", "")
        if job:
            command += ["--label", f"tunesaver.matching-job={job}"]
        command += ["-v", f"{export_playlist.ROOT}:/repo:ro", "-w", "/repo", "--entrypoint", "python", image,
                    "run_matching_tests.py", "--worker", *(["--events"] if EVENTS else []),
                    *(["--progress"] if PROGRESS and not EVENTS else [])]
        try:
            result = subprocess.run(command, input=json.dumps(payload), stdout=subprocess.PIPE, text=True)
        finally:
            if cidfile.exists():
                cid = cidfile.read_text().strip()
                if re.fullmatch(r"[0-9a-f]{64}", cid):
                    subprocess.run(["docker", "rm", "--force", cid], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=20, check=False)
    if result.returncode or not result.stdout.strip():
        raise ValueError("Appariement Docker interrompu ou indisponible.")
    return json.loads(result.stdout)


def run_cases(manifest, directory, matcher, cases=CASES, max_tracks=0):
    from build_test_viewer import build_viewer
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("collections"), list):
        raise ValueError("Le fichier doit contenir les collections du schema 1.")
    indexed = {}
    for collection in manifest["collections"]:
        key = (collection["source"], collection["kind"])
        if key in indexed:
            raise ValueError("Plusieurs collections correspondent au meme scenario.")
        indexed[key] = collection
    results, collections = [], []
    report = {"phase": "matching", "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "providers": ["tidal", "beatport", "qobuz"], "audio_downloaded": False,
              "planned": len(cases), "tested": 0, "passed": 0, "results": results, "max_tracks": max_tracks}

    def save():
        for filename, content in (("report.json", report), ("inputs.json", {"schema_version": 1, "collections": collections})):
            export_playlist.write_output(directory / filename, json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False) + "\n", force=True)
        build_viewer(directory)

    for source, kind, url in cases:
        identifier = case_id(source, kind)
        print(f"[{identifier}] Appariement...", flush=True)
        started = time.monotonic()
        emit({"type": "case_started", "case": identifier})
        result = {"source": source, "kind": kind, "url": url, "complete": False}
        try:
            original = indexed.get((source, kind))
            if original is None:
                raise ValueError("Metadonnees absentes : relancez d'abord ce test de lecture.")
            validate(original, source, kind)
            payload = matcher({**original, "_max_tracks": max_tracks}) if max_tracks else matcher(original)
            summary = payload["matching"]["summary"]
            for format_name in ("json", "txt"):
                export_playlist.write_output(directory / f"{identifier}.{format_name}", export_playlist.render(payload, format_name))
            collections.append(payload)
            result.update(status="ok", name=payload["name"], url=payload["url"], total=payload["total"], matched=summary["matched"],
                          complete=summary["matched"] == payload["total"], summary=summary,
                          files=[f"{identifier}.json", f"{identifier}.txt"])
            print(f"[{identifier}] {summary['matched']}/{payload['total']} trouves - "
                  f"{summary['not_found']} introuvables, {summary['ambiguous']} ambigus, {summary['error']} erreurs", flush=True)
        except Exception as exc:
            result.update(status="failed", error=str(exc) if type(exc) is ValueError else type(exc).__name__)
            print(f"[{identifier}] ECHEC - {result['error']}", flush=True)
        result["seconds"] = round(time.monotonic() - started, 2)
        results.append(result)
        report.update(tested=len(results), passed=sum(r["complete"] for r in results))
        save()
        emit({"type": "case_finished", "case": identifier, "result": result})
    if not cases:
        save()
    return report


def main(argv=None):
    global EVENTS, PROGRESS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="inputs.json du test de lecture (dernier par defaut)")
    parser.add_argument("--case", action="append", dest="cases", help="Scenario, ex. spotify-album ; option repetable")
    parser.add_argument("--docker", action="store_true")
    parser.add_argument("--container", default="tunesaver-web-1")
    parser.add_argument("--output-dir", type=Path, help="Dossier parent des resultats (matching/ a cote de l'entree par defaut)")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--events", action="store_true", help="Emettre la progression JSON sur stderr")
    parser.add_argument("--max-tracks", type=track_limit, default=0, help="Nombre de titres par playlist/album (0 = tous)")
    progress = parser.add_mutually_exclusive_group()
    progress.add_argument("--progress", dest="progress", action="store_true", help="Forcer la barre tqdm")
    progress.add_argument("--no-progress", dest="progress", action="store_false", help="Masquer la barre tqdm")
    parser.set_defaults(progress=None)
    args = parser.parse_args(argv)
    EVENTS = args.events
    PROGRESS = args.progress if args.progress is not None else sys.stderr.isatty()
    try:
        if args.worker:
            from catalog_matcher import Catalog
            payload = json.load(sys.stdin)
            with contextlib.redirect_stdout(sys.stderr):
                result = match_payload(payload, Catalog())
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 0
        cases = CASES
        if args.cases:
            unknown = set(args.cases) - {case_id(s, k) for s, k, _ in cases}
            if unknown:
                parser.error("Scenarios inconnus : " + ", ".join(sorted(unknown)))
            cases = [case for case in cases if case_id(*case[:2]) in args.cases]
        source = args.input
        if source is None:
            files = sorted((export_playlist.ROOT / "data" / "playlist-tests").glob("*/inputs.json"), key=lambda p: p.stat().st_mtime)
            if not files:
                raise ValueError("Aucun inputs.json disponible. Lancez run_playlist_tests.py.")
            source = files[-1]
        manifest = json.loads(source.read_text(encoding="utf-8"))
        root = args.output_dir or source.parent / "matching"
        root.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=root))
        emit({"type": "run_started", "directory": str(directory.resolve()), "planned": len(cases)})
        if args.docker:
            inspected = subprocess.run(["docker", "inspect", "--format", "{{.Image}}", args.container], capture_output=True, text=True, timeout=15)
            if inspected.returncode:
                raise ValueError("Conteneur TuneSaver inaccessible.")
            matcher = lambda payload: docker_match(payload, inspected.stdout.strip(), args.container)
        else:
            from catalog_matcher import Catalog
            catalog = Catalog()
            matcher = lambda payload: match_payload(payload, catalog)
        print(f"Entree : {source}\nResultats : {directory}", flush=True)
        report = run_cases(manifest, directory, matcher, cases, max_tracks=args.max_tracks)
        print(f"Bilan : {report['passed']}/{report['tested']} scenarios entierement apparies.\nVisualisation : {directory / 'viewer.html'}")
        return int(report["passed"] != report["tested"])
    except KeyboardInterrupt:
        print("Interrompu. Les scenarios termines restent sauvegardes.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Erreur : {str(exc) if type(exc) is ValueError else type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    sys.exit(main())
