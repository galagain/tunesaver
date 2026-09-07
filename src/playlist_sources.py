"""Playlist metadata readers and conservative cross-catalog matching."""

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

MAX_TRACKS = 500


class PlaylistError(Exception):
    pass


def playlist_source(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise PlaylistError("Lien de playlist invalide.")
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise PlaylistError("Utilisez un lien HTTPS de playlist.")
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if host == "open.spotify.com":
        match = re.fullmatch(r"/(?:intl-[a-z]+/)?(?:embed/)?playlist/([A-Za-z0-9]{22})", path)
        if match:
            return "spotify", match[1], f"https://open.spotify.com/playlist/{match[1]}"
    if host in {"soundcloud.com", "www.soundcloud.com"} and re.fullmatch(r"/[\w-]+/sets/[\w-]+", path):
        return "soundcloud", path, f"https://soundcloud.com{path}"
    if host in {"beatport.com", "www.beatport.com"}:
        match = re.fullmatch(r"/(?:[a-z]{2}/)?(?:catalog/)?(playlists?|charts?)/(?:[^/]+/)?([0-9]+)", path)
        if match:
            kind = "chart" if match[1].startswith("chart") else "playlist"
            return "beatport", f"{kind}:{match[2]}", f"https://www.beatport.com{path}"
    raise PlaylistError("Collez un lien complet de playlist Spotify, SoundCloud ou Beatport.")


def get_page(url, **kwargs):
    try:
        response = requests.get(url, timeout=(5, 25), allow_redirects=False, **kwargs)
        response.raise_for_status()
        if response.status_code != 200:
            raise PlaylistError("La playlist est inaccessible ou son lien a change.")
        return response
    except requests.RequestException as exc:
        raise PlaylistError("Playlist inaccessible : verifiez son lien et sa visibilite publique.") from exc


def spotify_playlist(playlist_id):
    client_id = os.environ.get("TUNESAVER_SPOTIFY_CLIENT_ID", "")
    secret = os.environ.get("TUNESAVER_SPOTIFY_CLIENT_SECRET", "")
    if client_id and secret:
        refresh = os.environ.get("TUNESAVER_SPOTIFY_REFRESH_TOKEN", "")
        body = {"grant_type": "refresh_token", "refresh_token": refresh} if refresh else {"grant_type": "client_credentials"}
        try:
            response = requests.post("https://accounts.spotify.com/api/token", auth=(client_id, secret),
                                     data=body, timeout=(5, 20), allow_redirects=False)
            response.raise_for_status()
            headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
            base = f"https://api.spotify.com/v1/playlists/{playlist_id}"
            meta = get_page(base, headers=headers).json()
            tracks = []
            offset = 0
            while True:
                page = get_page(f"{base}/items", headers=headers, params={"limit": 50, "offset": offset}).json()
                if page.get("total", 0) > MAX_TRACKS:
                    raise PlaylistError("La playlist depasse la limite de 500 titres.")
                for item in page["items"]:
                    track = item.get("track") or item.get("item") or {}
                    if track.get("type") == "track" and not item.get("is_local"):
                        tracks.append({"title": track["name"], "artist": ", ".join(a["name"] for a in track["artists"]),
                                       "duration": track.get("duration_ms", 0) / 1000})
                offset += len(page["items"])
                if not page.get("next"):
                    return {"name": meta["name"], "tracks": tracks}
                if not page["items"] or offset >= MAX_TRACKS:
                    raise PlaylistError("La playlist n'a pas pu etre recuperee integralement.")
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise PlaylistError("Connexion Spotify indisponible. Verifiez la configuration Spotify du serveur.") from exc

    try:
        html = get_page(f"https://open.spotify.com/embed/playlist/{playlist_id}").text
        script = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
        entity = json.loads(script.string)["props"]["pageProps"]["state"]["data"]["entity"]
        entries = entity["trackList"]
        # At exactly 100 entries the public preview may be truncated.
        if 0 < len(entries) < 100 and all(t.get("entityType") == "track" for t in entries):
            return {"name": entity["name"], "total": len(entries), "tracks": [
                {"title": t["title"], "artist": t.get("subtitle", ""), "duration": t.get("duration", 0) / 1000}
                for t in entries
            ]}
    except (PlaylistError, AttributeError, KeyError, TypeError, ValueError):
        pass
    return spotify_browser_playlist(playlist_id)


def spotify_browser_playlist(playlist_id):
    if not re.fullmatch(r"[A-Za-z0-9]{22}", playlist_id):
        raise PlaylistError("Identifiant Spotify invalide.")
    with tempfile.TemporaryDirectory(prefix="tunesaver-browser-") as directory:
        env = {**os.environ, "HOME": directory, "XDG_CACHE_HOME": directory,
               "XDG_CONFIG_HOME": directory, "XDG_STATE_HOME": directory, "TMPDIR": directory}
        command = [sys.executable, str(Path(__file__).with_name("spotify_browser.py")), playlist_id]
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, start_new_session=True, env=env)
            try:
                output, _ = process.communicate(timeout=160)
            finally:
                # Kill the isolated process group too, including Chromium children after a timeout/crash.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            payload = json.loads(output)
            if process.returncode or payload.get("error"):
                raise PlaylistError(payload.get("error") or "Lecture Spotify impossible.")
            if payload.get("total") != len(payload.get("tracks", [])):
                raise PlaylistError("La liste Spotify recue est incomplete.")
            return payload
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise PlaylistError("Lecture Spotify impossible pour le moment. Reessayez l'import.") from exc


def soundcloud_metadata(playlist, fetch_missing):
    entries = playlist.get("tracks") or []
    if max(len(entries), playlist.get("track_count") or 0) > MAX_TRACKS:
        raise PlaylistError("La playlist depasse la limite de 500 titres.")
    missing = [str(t["id"]) for t in entries if not t.get("title")]
    hydrated = {}
    for offset in range(0, len(missing), 50):
        hydrated.update({str(t["id"]): t for t in fetch_missing(missing[offset:offset + 50])})
    tracks = []
    for entry in entries:
        entry = hydrated.get(str(entry["id"]), entry)
        if not entry.get("title"):
            raise PlaylistError("SoundCloud contient un titre inaccessible : import incomplet, non lance.")
        title = entry["title"]
        publisher = entry.get("publisher_metadata") or {}
        artist = publisher.get("artist") or (entry.get("user") or {}).get("username", "")
        if not publisher.get("artist") and " - " in title:
            artist, title = title.split(" - ", 1)
        tracks.append({"title": title, "artist": artist, "duration": (entry.get("duration") or 0) / 1000})
    return {"name": playlist.get("title") or "SoundCloud", "tracks": tracks}


def soundcloud_playlist(url):
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.soundcloud import SoundcloudSetIE
    from yt_dlp.utils import DownloadError, ExtractorError

    # Reuse yt-dlp's authentication and playlist API, without requesting audio formats.
    class MetadataSetIE(SoundcloudSetIE):
        def _extract_set(self, playlist, token=None):
            return soundcloud_metadata(playlist, lambda ids: self._call_api(
                self._API_V2_BASE + "tracks", str(playlist["id"]),
                query={"ids": ",".join(ids)}, headers=self._HEADERS,
            ))

    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "socket_timeout": 20, "retries": 1}) as ydl:
            return MetadataSetIE(ydl).extract(url)
    except (DownloadError, ExtractorError, OSError, ValueError) as exc:
        raise PlaylistError("Lecture de la playlist SoundCloud impossible pour le moment.") from exc


def normalized_words(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def matching_track(track, candidates):
    title = normalized_words(track["title"])
    artist = set(normalized_words(track.get("artist", "")).split())
    if not title or not artist:
        return None
    matches = []
    for candidate in candidates:
        other_title = normalized_words(candidate.get("title", ""))
        mix = normalized_words(candidate.get("mix", ""))
        if mix and mix not in {"original mix", "original"} and mix not in other_title:
            other_title += " " + mix
        clean = lambda text: re.sub(r"\b(original mix|original version)\b", "", text).strip()
        if clean(title) != clean(other_title):
            continue
        other_artist = set(normalized_words(candidate.get("artist", "")).split())
        if not other_artist or len(artist & other_artist) / max(len(artist), len(other_artist)) < 0.8:
            continue
        duration = str(candidate.get("duration", ""))
        seconds = 0
        if re.fullmatch(r"\d+:\d{2}(?::\d{2})?", duration):
            for part in duration.split(":"):
                seconds = seconds * 60 + int(part)
        if track.get("duration") and seconds and abs(track["duration"] - seconds) > 12:
            continue
        matches.append(candidate)
    return matches[0] if matches else None
