#!/usr/bin/env python3
"""Export playlist metadata, without audio downloads or cross-catalog matching."""

import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import sys
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def read_playlist(url):
    import playlist_sources as sources

    provider, identifier, canonical = sources.playlist_source(url)
    if provider == "spotify" and "/album/" in canonical:
        payload = sources.spotify_album(canonical)
    elif provider == "spotify":
        from spotify_http import read_playlist as read_spotify
        payload = read_spotify(canonical)
    elif provider == "applemusic":
        payload = sources.apple_music_playlist(canonical)
    elif provider == "soundcloud":
        payload = sources.soundcloud_playlist(canonical)
    else:
        # Importing webapp does not start its server or access its database.
        import webapp
        payload = webapp.read_import_source(canonical)
    tracks = payload["tracks"]
    if payload.get("total", len(tracks)) != len(tracks):
        raise ValueError("Liste incomplete : aucun fichier ecrit.")
    rows = []
    for number, track in enumerate(tracks, 1):
        title = track.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Titre manquant : aucun fichier ecrit.")
        rows.append({"number": number, "title": title, "artist": track.get("artist", ""),
                     "duration_seconds": duration_seconds(track.get("duration")),
                     "url": track.get("source_url") or track.get("download_url") or "",
                     "unavailable": bool(track.get("unavailable"))})
    return {"name": payload["name"], "source": provider, "url": canonical,
            "total": len(rows), "tracks": rows}


def read_with_docker(url, container):
    inspected = subprocess.run(["docker", "inspect", "--format", "{{.Image}}", container],
                               capture_output=True, text=True, timeout=15, check=False)
    if inspected.returncode:
        raise ValueError("Conteneur TuneSaver inaccessible. Verifiez Docker et --container.")
    command = ["docker", "run", "--rm", "--volumes-from", container,
               "-v", f"{ROOT}:/repo:ro", "-w", "/repo",
               "-e", "MUSICDL_QOBUZ_SEARCH_SCRIPT=/repo/src/qobuz_search.py",
               "--entrypoint", "python", inspected.stdout.strip(),
               "export_playlist.py", url, "--format", "json", "-o", "-"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
    if result.returncode:
        # Forward only errors already sanitized by our child CLI, never Docker diagnostics.
        public_error = next((line.removeprefix("Erreur : ") for line in reversed(result.stderr.splitlines())
                             if line.startswith("Erreur : ")), None)
        if public_error:
            raise ValueError(public_error)
        raise ValueError("Export Docker impossible. Verifiez la visibilite de la playlist et la connexion de la source.")
    return json.loads(result.stdout)


def duration_seconds(value):
    try:
        if isinstance(value, str) and ":" in value:
            parts = value.split(":")
            if len(parts) not in (2, 3):
                return None
            seconds = 0.0
            for part in parts:
                seconds = seconds * 60 + float(part)
        else:
            seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds > 0 else None
    except (TypeError, ValueError):
        return None


def one_line(value):
    return " ".join(str(value).split())


def render(payload, format_name):
    if format_name == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if format_name == "csv":
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=["number", "title", "artist", "duration_seconds", "url", "unavailable"])
        writer.writeheader()
        writer.writerows(payload["tracks"])
        return output.getvalue()
    lines = [one_line(payload["name"]), payload["url"], f"{payload['total']} titres", ""]
    for track in payload["tracks"]:
        duration = track["duration_seconds"]
        clock = f" [{int(duration) // 60}:{int(duration) % 60:02d}]" if duration else ""
        artist = one_line(track["artist"])
        title = one_line(track["title"])
        unavailable = " [indisponible]" if track["unavailable"] else ""
        lines.append(f"{track['number']}. {artist + ' - ' if artist else ''}{title}{clock}{unavailable}")
    return "\n".join(lines) + "\n"


def write_output(path, content, *, force=False):
    # Publish only the complete export; never leave a partial file on interruption.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                     dir=path.parent, prefix=".playlist-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if force:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lister une playlist Spotify, Apple Music, SoundCloud, Beatport, TIDAL ou Qobuz, sans telecharger les sons.")
    parser.add_argument("url", help="Lien complet de la playlist")
    parser.add_argument("-o", "--output", default="playlist.txt", help="Fichier de sortie (defaut : playlist.txt), ou - pour le terminal")
    parser.add_argument("--format", choices=["txt", "json", "csv"], help="Defaut : extension du fichier, sinon txt")
    parser.add_argument("--force", action="store_true", help="Remplacer un fichier existant")
    parser.add_argument("--docker", action="store_true", help="Utiliser les dependances et sessions du serveur Docker")
    parser.add_argument("--container", default="tunesaver-web-1", help="Conteneur de reference pour --docker")
    args = parser.parse_args(argv)
    path = Path(args.output).expanduser()
    format_name = args.format or (path.suffix[1:].lower() if path.suffix[1:].lower() in {"txt", "json", "csv"} else "txt")
    try:
        if args.output != "-" and path.exists() and not args.force:
            raise FileExistsError("Le fichier existe deja. Choisissez un autre nom ou --force.")
        payload = read_with_docker(args.url, args.container) if args.docker else read_playlist(args.url)
        content = render(payload, format_name)
        if args.output == "-":
            sys.stdout.write(content)
        else:
            write_output(path, content, force=args.force)
            print(f"{payload['total']} titres ecrits dans {path}", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("Lecture annulee.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Unexpected provider exceptions may contain token-bearing request URLs.
        public = type(exc) in {ValueError, FileExistsError, FileNotFoundError, PermissionError}
        public = public or type(exc).__name__ in {"PlaylistError", "SourceError"}
        message = str(exc) if public else f"Lecture impossible ({type(exc).__name__}). Verifiez les dependances et la configuration de la source."
        print(f"Erreur : {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
