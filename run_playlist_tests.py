#!/usr/bin/env python3
"""Test playlists, albums, direct tracks and manual text without downloading audio."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time

import export_playlist


PLAYLISTS = [
    ("spotify", "https://open.spotify.com/playlist/57HAIqxdHbi332994xcOKc"),
    ("applemusic", "https://music.apple.com/sn/playlist/jazz-house/pl.2e69eda9c8af461e8924dfebbcdf9794?l=fr-FR"),
    ("soundcloud", "https://soundcloud.com/delicieuse-musique/sets/jazz-house"),
    ("beatport", "https://www.beatport.com/fr/chart/best-of-classics-techno/820196"),
    ("tiddl", "https://tidal.com/playlist/0bfac8e5-4ff8-44e4-b112-5fb71d39bcc1"),
    ("qobuz", "https://www.qobuz.com/fr-fr/playlists/jazz-n-electro/1270781"),
]
ALBUMS = [
    ("spotify", "https://open.spotify.com/album/4m2880jivSbbyEGAKfITCa"),
    ("applemusic", "https://music.apple.com/us/album/random-access-memories/617154241"),
    ("soundcloud", "https://soundcloud.com/daftpunkofficialmusic/sets/random-access-memories-10th"),
    ("tiddl", "https://tidal.com/album/20115556"),
    ("beatport", "https://www.beatport.com/release/random-access-memories/1091742"),
    ("qobuz", "https://www.qobuz.com/fr-fr/album/random-access-memories-daft-punk/0886443927087"),
]
TRACKS = [
    ("spotify", "https://open.spotify.com/track/0dEIca2nhcxDUV8C5QkPYb"),
    ("applemusic", "https://music.apple.com/us/album/give-life-back-to-music/617154241?i=617154353"),
    ("soundcloud", "https://soundcloud.com/daftpunkofficialmusic/give-life-back-to-music"),
    ("tiddl", "https://tidal.com/track/20115557"),
    ("beatport", "https://www.beatport.com/track/give-life-back-to-music/4400239"),
    ("qobuz", "https://play.qobuz.com/track/9140024"),
]
CASES = [(source, "playlist", url) for source, url in PLAYLISTS] + [
    (source, "album", url) for source, url in ALBUMS] + [
    (source, "track", url) for source, url in TRACKS] + [
    ("manual", "track", "Hot Since 82 - Buggin' (feat. Jem Cooke)")]


def validate(payload, source, kind):
    tracks = payload["tracks"]
    if (payload.get("schema_version") != 1 or payload.get("kind") != kind
            or payload["source"] != source or not tracks or payload["total"] != len(tracks)):
        raise ValueError("Schema, type, compte ou provenance incoherent.")
    fields = {"number", "title", "artist", "duration_seconds", "url", "unavailable", "mix", "album", "target", "requires_matching"}
    for index, track in enumerate(tracks, 1):
        if set(track) != fields or track["number"] != index or not track["title"].strip():
            raise ValueError("Format de morceau invalide.")
        native = source in {"tiddl", "beatport", "qobuz"} and not track["unavailable"]
        if native:
            target = track["target"] or {}
            if (target.get("provider") != ("tidal" if source == "tiddl" else source)
                    or target.get("kind") != "track" or target.get("method") != "direct"
                    or target.get("url") != track["url"] or track["requires_matching"] is not False):
                raise ValueError("Le lien natif doit etre deja resolu, sans appariement.")
        elif track["target"] is not None or track["requires_matching"] is not True:
            raise ValueError("La source externe ne doit pas etre marquee comme deja resolue.")


def run_tests(directory, reader):
    results = []
    collections = []
    for source, kind, url in CASES:
        case_id = source if kind == "playlist" else f"{source}-{kind}"
        print(f"[{case_id}] Lecture {kind}...", flush=True)
        started = time.monotonic()
        result = {"source": source, "kind": kind, "url": url}
        try:
            payload = reader(url)
            tracks = payload["tracks"]
            validate(payload, source, kind)
            for format_name in ("txt", "json"):
                export_playlist.write_output(directory / f"{case_id}.{format_name}",
                                             export_playlist.render(payload, format_name))
            result.update(status="ok", name=payload["name"], total=len(tracks),
                          files=[f"{case_id}.txt", f"{case_id}.json"])
            collections.append(payload)
        except Exception as exc:
            public = type(exc) is ValueError or type(exc).__name__ in {"PlaylistError", "SourceError"}
            result.update(status="failed", error=str(exc) if public else type(exc).__name__)
        result["seconds"] = round(time.monotonic() - started, 2)
        results.append(result)
        detail = f"{result['total']} titres" if result["status"] == "ok" else result["error"]
        print(f"[{case_id}] {result['status'].upper()} - {detail} ({result['seconds']} s)", flush=True)
    report = {"created_at": datetime.now(timezone.utc).isoformat(),
              "passed": sum(item["status"] == "ok" for item in results),
              "tested": len(results), "results": results}
    export_playlist.write_output(directory / "report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    export_playlist.write_output(directory / "inputs.json", json.dumps(
        {"schema_version": 1, "collections": collections}, ensure_ascii=False, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", action="store_true", help="Utiliser l'environnement Docker TuneSaver")
    parser.add_argument("--container", default="tunesaver-web-1", help="Conteneur de reference pour --docker")
    parser.add_argument("--output-dir", type=Path, default=export_playlist.ROOT / "data" / "playlist-tests",
                        help="Dossier contenant les rapports et les listes (un sous-dossier par execution)")
    args = parser.parse_args(argv)
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        prefix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
        directory = Path(tempfile.mkdtemp(prefix=prefix, dir=args.output_dir))
        reader = (lambda url: export_playlist.read_with_docker(url, args.container)) if args.docker else export_playlist.read_playlist
        report = run_tests(directory, reader)
        print(f"\nBilan : {report['passed']}/{report['tested']} tests reussis.\nResultats : {directory}")
        return int(report["passed"] != report["tested"])
    except KeyboardInterrupt:
        print("Tests interrompus.", file=sys.stderr)
        return 130
    except OSError as exc:
        print(f"Impossible d'ecrire les resultats ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
