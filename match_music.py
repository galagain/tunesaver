#!/usr/bin/env python3
"""Chercher des appariements TIDAL, Beatport puis Qobuz, sans audio."""

import argparse
import contextlib
import json
from pathlib import Path
import subprocess
import sys

import export_playlist


def run(inputs):
    from catalog_matcher import Catalog, read_input, match_collection, safe_error
    catalog = Catalog()
    results = []
    for number, value in enumerate(inputs, 1):
        print(f"[{number}/{len(inputs)}] {value}", file=sys.stderr, flush=True)
        try:
            collection = read_input(value, catalog)
            print(f"  {collection['kind']} : {len(collection['tracks'])} titres", file=sys.stderr, flush=True)
            result = match_collection(collection, catalog,
                                      progress=lambda message: print(message, file=sys.stderr, flush=True))
            print(f"  {result['matched']}/{result['total']} appariements", file=sys.stderr, flush=True)
            results.append({"input": value, "status": "ok", **result})
        except Exception as exc:
            results.append({"input": value, "status": "error", "error": safe_error(exc)})
    return {"providers": ["tidal", "beatport", "qobuz"], "audio_downloaded": False, "results": results}


def run_docker(inputs, container):
    inspected = subprocess.run(["docker", "inspect", "--format", "{{.Image}}", container],
                               capture_output=True, text=True, timeout=15)
    if inspected.returncode:
        raise ValueError("Conteneur TuneSaver inaccessible.")
    command = ["docker", "run", "--rm", "-i", "--volumes-from", container,
               "-v", f"{export_playlist.ROOT}:/repo:ro", "-w", "/repo",
               "-e", "MUSICDL_QOBUZ_SEARCH_SCRIPT=/repo/src/qobuz_search.py",
               "--entrypoint", "python", inspected.stdout.strip(), "match_music.py", "--stdin-json", "-o", "-"]
    # No whole-playlist timeout or size cap; individual metadata requests are bounded.
    result = subprocess.run(command, input=json.dumps(inputs), stdout=subprocess.PIPE, text=True)
    if result.returncode not in (0, 1) or not result.stdout.strip():
        raise ValueError("Recherche Docker interrompue ou indisponible.")
    return json.loads(result.stdout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*", help="Liens ou noms 'Artiste - Titre'")
    parser.add_argument("--file", type=Path, help="Fichier UTF-8, une entree par ligne")
    parser.add_argument("--docker", action="store_true", help="Utiliser les sessions du serveur Docker")
    parser.add_argument("--container", default="tunesaver-web-1")
    parser.add_argument("--stdin-json", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("-o", "--output", default="matches.json", help="Rapport JSON ou - pour stdout")
    parser.add_argument("--force", action="store_true", help="Remplacer le rapport existant")
    args = parser.parse_args(argv)
    try:
        inputs = list(args.inputs)
        if args.file:
            inputs.extend(line.strip() for line in args.file.read_text(encoding="utf-8").splitlines()
                          if line.strip() and not line.lstrip().startswith("#"))
        if args.stdin_json:
            entries = json.load(sys.stdin)
            if not isinstance(entries, list) or not all(isinstance(x, str) and x.strip() for x in entries):
                raise ValueError("Liste JSON d'entrees invalide.")
            inputs.extend(entries)
        if not inputs:
            parser.error("Donnez au moins un lien, un nom ou --file.")
        if args.output != "-" and Path(args.output).exists() and not args.force:
            raise ValueError("Le rapport existe deja. Choisissez un autre nom ou --force.")
        with contextlib.redirect_stdout(sys.stderr):
            report = run_docker(inputs, args.container) if args.docker else run(inputs)
        content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output == "-":
            print(content, end="")
        else:
            export_playlist.write_output(Path(args.output), content, force=args.force)
            print(f"Rapport : {args.output}", file=sys.stderr)
        return int(any(r["status"] == "error" or any(t["status"] == "error" for t in r.get("tracks", []))
                       for r in report["results"]))
    except KeyboardInterrupt:
        print("Recherche interrompue.", file=sys.stderr)
        return 130
    except Exception as exc:
        public = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(f"Erreur : {public}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
