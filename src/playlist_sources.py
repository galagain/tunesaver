"""Playlist metadata readers and conservative cross-catalog matching."""

import base64
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

SOURCE_LABELS = {"applemusic": "Apple Music", "tiddl": "TIDAL", "qobuz": "Qobuz"}


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
    if host in {"tidal.com", "www.tidal.com", "listen.tidal.com"}:
        match = re.fullmatch(r"/(?:browse/)?playlist/([0-9a-fA-F-]{36})", path)
        if match:
            return "tiddl", match[1], f"https://tidal.com/playlist/{match[1]}"
    if host in {"qobuz.com", "www.qobuz.com", "play.qobuz.com", "open.qobuz.com"}:
        match = re.fullmatch(r"/(?:[a-z]{2}-[a-z]{2}/)?playlists?/(?:[^/]+/)?([0-9]+)", path)
        if match:
            return "qobuz", match[1], f"https://play.qobuz.com/playlist/{match[1]}"
    if host == "open.spotify.com":
        match = re.fullmatch(r"/(?:intl-[a-z]+/)?(?:embed/)?(playlist|album)/([A-Za-z0-9]{22})", path)
        if match:
            return "spotify", match[2], f"https://open.spotify.com/{match[1]}/{match[2]}"
    if host == "music.apple.com":
        match = re.fullmatch(r"/([a-z]{2})/playlist/(?:[^/]+/)?(pl\.[A-Za-z0-9-]+)", path)
        if match:
            return "applemusic", match[2], f"https://music.apple.com{path}"
        match = re.fullmatch(r"/[a-z]{2}/album/(?:[^/]+/)?([0-9]+)", path)
        if match and "i" not in parse_qs(parsed.query, keep_blank_values=True):
            return "applemusic", match[1], f"https://music.apple.com{path}"
    if host in {"soundcloud.com", "www.soundcloud.com"} and re.fullmatch(r"/[\w-]+/sets/[\w-]+", path):
        return "soundcloud", path, f"https://soundcloud.com{path}"
    if host in {"beatport.com", "www.beatport.com"}:
        match = re.fullmatch(r"/(?:[a-z]{2}/)?(?:catalog/)?(playlists?|charts?)/(?:[^/]+/)?([0-9]+)", path)
        if match:
            kind = "chart" if match[1].startswith("chart") else "playlist"
            return "beatport", f"{kind}:{match[2]}", f"https://www.beatport.com{path}"
    raise PlaylistError("Collez un lien complet de playlist Apple Music, Spotify, SoundCloud, Beatport, TIDAL ou Qobuz.")


def native_playlist(provider, identifier, fetch):
    """Read all native playlist pages; fetch performs authenticated metadata requests only."""
    tracks, offset, total, name = [], 0, None, ""
    while True:
        page = fetch(offset)
        count = page.get("numberOfTracks") if provider == "tiddl" else page.get("tracks_count")
        if type(count) is not int or count < 0 or (total is not None and count != total):
            raise PlaylistError("Le nombre de titres a change ou est invalide. Relancez la lecture.")
        total = count
        name = name or page.get("title") or page.get("name") or SOURCE_LABELS[provider]
        entries = page.get("items", []) if provider == "tiddl" else page.get("tracks", {}).get("items", [])
        for entry in entries:
            track = entry.get("item", entry) if provider == "tiddl" else entry
            track_id = str(track.get("id", ""))
            title = track.get("title")
            if not track_id.isdigit() or not title:
                raise PlaylistError("Un titre de la playlist est inaccessible. Liste incomplete.")
            if provider == "tiddl":
                artist = ", ".join(a["name"] for a in track.get("artists", []))
                url = f"https://listen.tidal.com/track/{track_id}"
            else:
                artist = (track.get("performer") or {}).get("name", "")
                url = f"https://play.qobuz.com/track/{track_id}"
            version = track.get("version")
            if version and version.casefold() not in title.casefold():
                title = f"{title} ({version})"
            tracks.append({"title": title, "artist": artist, "duration": track.get("duration", 0),
                           "provider": provider, "download_url": url, "source_url": url})
        offset += len(entries)
        if offset == total:
            return {"name": name, "total": total, "tracks": tracks}
        if not entries or offset > total:
            raise PlaylistError(f"Lecture incomplete ({offset}/{total} titres).")


def apple_music_playlist(url):
    provider, identifier, url = playlist_source(url)
    if provider != "applemusic":
        raise PlaylistError("Lien Apple Music invalide.")
    try:
        # Apple redirects ID-only or renamed playlist URLs to their current slug.
        for _ in range(4):
            response = requests.get(url, timeout=(5, 25), allow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308):
                source, target_id, target = playlist_source(urljoin(url, response.headers.get("Location", "")))
                if source != "applemusic" or target_id != identifier:
                    raise PlaylistError("Redirection Apple Music invalide.")
                url = target
                continue
            response.raise_for_status()
            if response.status_code != 200:
                raise PlaylistError("La playlist Apple Music est inaccessible.")
            response.encoding = "utf-8"
            return apple_music_metadata(response.text, identifier)
        raise PlaylistError("Trop de redirections Apple Music.")
    except requests.RequestException as exc:
        raise PlaylistError("Playlist Apple Music inaccessible : verifiez son lien et sa visibilite publique.") from exc


def apple_music_metadata(html, identifier):
    try:
        script = BeautifulSoup(html, "html.parser").find("script", id="serialized-server-data")
        payload = json.loads(script.string)
        page = next(item["data"] for item in payload["data"]
                    if item.get("intent", {}).get("$kind") == (
                        "AlbumDetailPageIntent" if identifier.isdigit() else "PlaylistDetailPageIntent")
                    and item["intent"]["contentDescriptor"]["identifiers"]["storeAdamID"] == identifier)
        sections = page["sections"]
        header = next(item for section in sections if section.get("itemKind") == "containerDetailHeaderLockup"
                      for item in section["items"]
                      if item.get("contentDescriptor", {}).get("identifiers", {}).get("storeAdamID") == identifier)
        total = header["trackCount"]
        if type(total) is not int or total < 1:
            raise PlaylistError("La playlist Apple Music ne contient aucun titre accessible.")
        entries = [item for section in sections
                   if section.get("itemKind") == "trackLockup" and section.get("id") == f"track-list - {identifier}"
                   for item in section["items"]]
        if len(entries) != total:
            raise PlaylistError(f"Lecture Apple Music incomplete ({len(entries)}/{total} titres). Aucun telechargement lance.")
        tracks = []
        for item in entries:
            title = item.get("title")
            artist = item.get("artistName") or ", ".join(link["title"] for link in (item.get("subtitleLinks") or []))
            duration = item.get("duration")
            if (item.get("contentDescriptor", {}).get("kind") != "song"
                    or not isinstance(title, str) or not title.strip()
                    or not isinstance(artist, str) or not artist.strip()
                    or not isinstance(duration, (int, float)) or duration <= 0):
                raise PlaylistError("Un titre Apple Music est inaccessible ou incomplet. Import non lance.")
            tracks.append({"title": title.strip(), "artist": artist.strip(), "duration": duration / 1000})
        return {"name": header["title"], "total": total, "tracks": tracks}
    except (AttributeError, KeyError, TypeError, ValueError, StopIteration) as exc:
        raise PlaylistError("Lecture Apple Music impossible : playlist privee, indisponible ou format de page modifie.") from exc


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
            total = None
            while True:
                page = get_page(f"{base}/items", headers=headers, params={"limit": 50, "offset": offset}).json()
                count = page.get("total")
                if type(count) is not int or count < 0 or (total is not None and count != total):
                    raise PlaylistError("Le nombre de titres Spotify a change ou est invalide.")
                total = count
                for item in page["items"]:
                    track = item.get("track") or item.get("item") or {}
                    if track.get("type") == "track" and not item.get("is_local"):
                        tracks.append({"title": track["name"], "artist": ", ".join(a["name"] for a in track["artists"]),
                                       "duration": track.get("duration_ms", 0) / 1000})
                    else:
                        tracks.append({"title": track.get("name") or f"Titre Spotify indisponible (position {len(tracks) + 1})",
                                       "artist": "", "duration": 0, "unavailable": True})
                offset += len(page["items"])
                if offset > total:
                    raise PlaylistError("Pagination Spotify incoherente.")
                if not page.get("next"):
                    if offset != total:
                        raise PlaylistError(f"Lecture Spotify incomplete ({offset}/{total} titres).")
                    return {"name": meta["name"], "total": total, "tracks": tracks}
                if not page["items"]:
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


def spotify_album(url):
    provider, identifier, canonical = playlist_source(url)
    if provider != "spotify" or "/album/" not in canonical:
        raise PlaylistError("Lien d'album Spotify invalide.")
    return spotify_album_metadata(get_page(canonical).text, identifier)


def spotify_album_metadata(html, identifier):
    """Read the album itself, excluding related releases and audio previews."""
    try:
        script = BeautifulSoup(html, "html.parser").find("script", id="initialState")
        state = json.loads(base64.b64decode(script.string, validate=True))
        album = state["entities"]["items"][f"spotify:album:{identifier}"]
        page = album["tracksV2"]
        total = page["totalCount"]
        entries = page["items"]
        if (type(total) is not int or total < 1 or len(entries) != total
                or page.get("pagingInfo", {}).get("offset", 0) != 0):
            raise PlaylistError("Lecture de l'album Spotify incomplete. Aucun import lance.")
        tracks = []
        for entry in entries:
            track = entry["track"]
            title = track["name"]
            artist = ", ".join(a["profile"]["name"] for a in track["artists"]["items"])
            duration = track["duration"]["totalMilliseconds"]
            if not title or not artist or not isinstance(duration, (int, float)) or duration <= 0:
                raise PlaylistError("Un titre de l'album Spotify est incomplet.")
            tracks.append({"title": title, "artist": artist, "duration": duration / 1000,
                           "source_url": f"https://open.spotify.com/track/{track['id']}"})
        return {"name": album["name"], "total": total, "tracks": tracks}
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PlaylistError("Album Spotify inaccessible ou format de page modifie.") from exc


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
    total = playlist.get("track_count", len(entries))
    if len(entries) != total:
        raise PlaylistError(f"Lecture SoundCloud incomplete ({len(entries)}/{total} titres).")
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
    return {"name": playlist.get("title") or "SoundCloud", "tracks": tracks, "total": total}


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


def split_featured_artists(value):
    guests = set()

    def extract(match):
        credit = match.group(1).strip()
        # Ambiguous credits containing version words must not erase a remix label.
        if re.search(r"\b(remix|edit|dub|mix|live|instrumental|acoustic|extended)\b", credit, re.I):
            return match.group(0)
        guests.update(normalized_words(credit).split())
        return " "

    marker = r"(?:feat\.?|ft\.?|featuring)\s+"
    value = re.sub(r"[\(\[]\s*" + marker + r"([^()\[\]]+)[\)\]]", extract, value, flags=re.I)
    value = re.sub(r"\s+" + marker + r"([^()\[\]]+?)(?=\s+[-\u2013\u2014]\s+|[\(\[]|$)",
                   extract, value, flags=re.I)
    return normalized_words(value), guests


def matching_metadata(track):
    title, guests = split_featured_artists(track.get("title", ""))
    artist, artist_guests = split_featured_artists(track.get("artist", ""))
    mix = normalized_words(track.get("mix", ""))
    if mix and mix not in {"original mix", "original"} and mix not in title:
        title += " " + mix
    title = " ".join(re.sub(r"\b(original mix|original version)\b", "", title).split())
    return title, set(artist.split()), guests | artist_guests


def matching_duration(value):
    try:
        if isinstance(value, str) and re.fullmatch(r"\d+:\d{2}(?::\d{2})?", value):
            seconds = 0
            for part in value.split(":"):
                seconds = seconds * 60 + int(part)
        else:
            seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds > 0 else None
    except (TypeError, ValueError):
        return None


def split_release_edition(title):
    match = re.search(r"\s+(radio edit|radio version|extended mix|extended version|single version|album version|edit)$", title)
    return (title[:match.start()], match[1]) if match else (title, "")


def matching_track(track, candidates):
    title, artist, guests = matching_metadata(track)
    if not title or not artist:
        return None
    reference_duration = matching_duration(track.get("duration"))
    matches = []
    for index, candidate in enumerate(candidates):
        other_title, other_artist, other_guests = matching_metadata(candidate)
        seconds = matching_duration(candidate.get("duration"))
        difference = abs(reference_duration - seconds) if reference_duration and seconds else None
        if difference is not None and difference > 12:
            continue
        if title != other_title:
            base, edition = split_release_edition(title)
            other_base, other_edition = split_release_edition(other_title)
            # Some catalogs omit standard release labels. Accept a missing label
            # only with near-identical durations; never equate two explicit editions.
            if (base != other_base or bool(edition) == bool(other_edition)
                    or difference is None or difference > 2):
                continue
        if guests and other_guests and len(guests & other_guests) / max(len(guests), len(other_guests)) < 0.8:
            continue
        # A credited guest can live in the title, the artist list, or be omitted
        # by the source. Unknown additional artists still fail the original check.
        primary = artist - (guests | other_guests)
        other_primary = other_artist - (guests | other_guests)
        if (not primary or not other_primary
                or len(primary & other_primary) / max(len(primary), len(other_primary)) < 0.8):
            continue
        score = difference if difference is not None else (math.inf if reference_duration else 0)
        matches.append((score, index, candidate))
    return min(matches, key=lambda match: match[:2])[2] if matches else None
