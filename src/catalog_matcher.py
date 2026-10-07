"""Metadata-only catalog matching. No queue, database or audio operations."""

import configparser
from collections import Counter
import json
import re
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

import playlist_sources as sources

PROVIDERS = ("tidal", "beatport", "qobuz")


class MatchError(ValueError):
    pass


def direct_target(track):
    provider = {"tiddl": "tidal"}.get(track.get("provider"), track.get("provider"))
    url = track.get("download_url") or track.get("source_url") or ""
    host = urlparse(url).hostname
    hosts = {"tidal": {"tidal.com", "listen.tidal.com", "www.tidal.com"},
             "beatport": {"beatport.com", "www.beatport.com"},
             "qobuz": {"play.qobuz.com", "open.qobuz.com", "www.qobuz.com", "qobuz.com"}}
    parsed = urlparse(url)
    if (provider in hosts and host in hosts[provider] and parsed.scheme == "https"
            and not parsed.username and not parsed.password and parsed.port in (None, 443)
            and re.fullmatch(r"/(?:browse/|[a-z]{2}(?:-[a-z]{2})?/)?track/(?:[^/]+/)?[0-9]+/?", parsed.path)
            and not track.get("unavailable")):
        return {"provider": provider, "kind": "track", "url": url, "method": "direct"}
    return None


def match_track(track, candidates):
    def metadata(item):
        mix = item.get("mix", "")
        if re.match(r"^(?:feat\.?|ft\.?|featuring)\s+", mix, re.I):
            return {**item, "title": f"{item['title']} ({mix})", "mix": ""}
        return item
    track = metadata(track)
    prepared = [metadata(c) for c in candidates]
    if track.get("artist"):
        found = sources.matching_track(track, prepared)
        return (candidates[prepared.index(found)] if found else None), False
    possible = [c for c in prepared if c.get("artist") and sources.matching_track(
        {**track, "artist": c["artist"]}, [c])]
    identities = {(sources.matching_metadata(c)[0], sources.normalized_words(c["artist"])) for c in possible}
    if len(identities) != 1:
        return None, bool(possible)
    found = sources.matching_track({**track, "artist": possible[0]["artist"]}, possible)
    return candidates[prepared.index(found)], False


def match_album(collection, album):
    if sources.normalized_words(collection["name"]) != sources.normalized_words(album["name"]):
        return None
    remaining = list(album["tracks"])
    if len(remaining) != len(collection["tracks"]):
        return None
    matches = []
    for track in collection["tracks"]:
        if track.get("unavailable"):
            return None
        found, ambiguous = match_track(track, remaining)
        if not found or ambiguous:
            return None
        matches.append(found)
        remaining.remove(found)
    return matches


def queries(track):
    mix = track.get("mix", "")
    if mix and mix.casefold() not in track["title"].casefold():
        track = {**track, "title": f"{track['title']} ({mix})"}
    full = f"{track.get('artist', '')} {track['title']}".strip()
    title, guests = sources.split_featured_artists(track["title"])
    lead = ", ".join(a.strip() for a in track.get("artist", "").split(",")
                     if not set(sources.normalized_words(a).split()) <= guests)
    simplified = f"{lead} {title}".strip()
    credits = sources.artist_credits(track.get('artist', ''))
    primary = credits[0] if credits else ''
    primary_query = f'{primary} {title}'.strip()
    variants = [full, simplified, primary_query]
    base, edition = sources.split_release_edition(title)
    if edition and primary:
        variants.append(f'{primary} {base}')
    if primary:
        variants.append(title)
    unique = {}
    for query in variants:
        if query.strip():
            unique.setdefault(sources.normalized_words(query), query)
    return list(unique.values())


def match_collection(collection, catalog, progress=None, on_event=None, max_tracks=0):
    if (collection.get("provider") == "soundcloud"
            or urlparse(collection.get("source", "")).hostname in {"soundcloud.com", "www.soundcloud.com"}):
        collection = {**collection, "tracks": [sources.normalize_soundcloud_track(t) for t in collection["tracks"]]}

    def event(kind, **values):
        if on_event:
            on_event({"type": kind, **values})

    def update(row, final=False):
        match = row["match"]
        errors = [a.get("error") for a in row["attempts"] if a.get("error")]
        event("track_updated", number=row["number"], status=row["status"], final=final,
              candidate=match,
              target=direct_target(match) if match else None,
              method=match.get("method") if match else None, error="; ".join(errors))
    rows = [{"number": n, "input": track, "status": "unavailable" if track.get("unavailable") else "not_found",
             "match": None, "attempts": []} for n, track in enumerate(collection["tracks"][:max_tracks or None], 1)]
    album_attempts = []
    for row in rows:
        target = direct_target(row["input"])
        if target:
            row.update(status="matched", match={**row["input"], "provider": target["provider"],
                                               "source_url": target["url"], "download_url": target["url"],
                                               "method": "direct"})
            update(row, final=True)
        elif row["status"] == "unavailable":
            update(row, final=True)
    for provider in PROVIDERS:
        pending = [row for row in rows if row["match"] is None and row["status"] != "unavailable"]
        if not pending:
            break
        if progress:
            progress(f"  {provider}: {len(pending)} titres restants")
        event("provider", provider=provider)
        if collection["kind"] == "album":
            event("album_started", provider=provider)
            attempt = {"provider": provider, "status": "not_found"}
            album_attempts.append(attempt)
            try:
                for album in catalog.search_albums(provider, collection):
                    matches = match_album(collection, album)
                    if matches:
                        attempt.update(status="matched", url=album["url"])
                        for row in pending:
                            row.update(status="matched", match={**matches[row["number"] - 1],
                                       "provider": provider, "method": "album", "album_url": album["url"]})
                            update(row, final=True)
                        break
            except Exception as exc:
                attempt.update(status="error", error=safe_error(exc))
            event("album_updated", **attempt)
            if attempt["status"] == "matched":
                continue
        for row in pending:
            event("track_started", number=row["number"], provider=provider)
            attempt = {"provider": provider, "status": "not_found", "queries": []}
            row["attempts"].append(attempt)
            candidates = []
            try:
                for query in queries(row["input"]):
                    attempt["queries"].append(query)
                    candidates.extend(catalog.search_tracks(provider, query))
                    attempt['candidates_checked'] = len(candidates)
                    found, ambiguous = match_track(row["input"], candidates)
                    if not found and isinstance(catalog, Catalog):
                        candidates = catalog.with_verified_credits(provider, row['input'], candidates)
                        found, ambiguous = match_track(row['input'], candidates)
                    if found:
                        row.update(status="matched", match={**found, "provider": provider, "method": "track"})
                        attempt["status"] = "matched"
                        break
                    if ambiguous:
                        row["status"] = attempt["status"] = "ambiguous"
                        attempt["candidates"] = candidates
            except Exception as exc:
                attempt.update(status="error", error=safe_error(exc))
            update(row, final=row["match"] is not None)
    for row in rows:
        if row["status"] == "not_found" and any(a["status"] == "error" for a in row["attempts"]):
            row["status"] = "error"
        update(row, final=True)
    return {"name": collection["name"], "kind": collection["kind"], "source": collection.get("source", ""),
            "total": len(rows), "matched": sum(r["status"] == "matched" for r in rows),
            "album_attempts": album_attempts, "tracks": rows}


def safe_error(exc):
    # Third-party exceptions can contain authenticated URLs. Never expose them.
    return str(exc) if isinstance(exc, (MatchError, sources.PlaylistError)) else type(exc).__name__


class Catalog:
    def __init__(self):
        self.cache = {}
        self.unavailable = {}

    @property
    def web(self):
        import webapp
        return webapp

    def api(self, provider, path, **params):
        if provider in self.unavailable:
            raise MatchError(self.unavailable[provider])
        try:
            return self._api(provider, path, **params)
        except (requests.RequestException, OSError) as exc:
            message = f"{provider}: connexion ou configuration indisponible ({type(exc).__name__})."
            self.unavailable[provider] = message
            raise MatchError(message) from None

    def _api(self, provider, path, **params):
        key = (provider, path, tuple(sorted(params.items())))
        if key in self.cache:
            return self.cache[key]
        headers, proxies = {}, None
        if provider == "tidal":
            auth = self.web.current_tidal_auth()
            headers = {"Authorization": f"Bearer {auth['token']}"}
            params = {"countryCode": auth["country_code"], **params}
            url = "https://api.tidal.com/v1/" + path
        elif provider == "beatport":
            token = self.web.valid_beatport_token()
            if not token:
                self.web.beatport_helper_search("Daft Punk", proxy=self.web.configured_beatport_proxy())
                token = self.web.valid_beatport_token()
            if not token:
                raise MatchError("Session Beatport indisponible.")
            headers = {"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}
            proxy = self.web.configured_beatport_proxy()
            proxies = {"https": proxy} if proxy else None
            url = "https://api.beatport.com/v4/catalog/" + path
        else:
            config = configparser.ConfigParser()
            with self.web.musicdl.QOBUZ_CONFIG_PATH.open(encoding="utf-8") as handle:
                config.read_file(handle)
            headers = {"X-App-Id": config["DEFAULT"]["app_id"],
                       "X-User-Auth-Token": config["DEFAULT"]["user_auth_token"]}
            url = "https://www.qobuz.com/api.json/0.2/" + path
        response = requests.get(url, params=params, headers=headers, proxies=proxies,
                                timeout=(5, 25), allow_redirects=False)
        if response.status_code == 401 and provider == "tidal":
            auth = self.web.refresh_tidal_auth(force=True, rejected_token=auth["token"])
            headers["Authorization"] = f"Bearer {auth['token']}"
            response = requests.get(url, params=params, headers=headers, timeout=(5, 25), allow_redirects=False)
        if response.status_code != 200:
            message = f"{provider}: metadonnees indisponibles (HTTP {response.status_code})."
            if response.status_code in {401, 403, 429} or response.status_code >= 500:
                self.unavailable[provider] = message
            raise MatchError(message)
        result = response.json()
        self.cache[key] = result
        if len(self.cache) > 256:
            self.cache.pop(next(iter(self.cache)))
        return result

    def normalize(self, provider, tracks):
        # Normalize individually: UI search limits must not truncate an album.
        normalizer = {"tidal": self.web.normalize_tidal_results, "beatport": self.web.normalize_beatport_results,
                      "qobuz": self.web.normalize_qobuz_results}[provider]
        output = []
        for track in tracks:
            output.extend(normalizer({"tracks": [track] if provider == "beatport" else {"items": [track]}}))
        return output

    def search_tracks(self, provider, query):
        if provider == "tidal":
            data = self.api(provider, "search/tracks", query=query, limit=50)
            tracks = data["items"]
        elif provider == "beatport":
            data = self.api(provider, "search/", q=query)
            tracks = data.get("tracks", [])
        else:
            data = self.api(provider, "track/search", query=query, limit=50)
            tracks = data["tracks"]["items"]
        return self.normalize(provider, tracks)

    def with_verified_credits(self, provider, source, candidates):
        if provider != 'tidal':
            return candidates
        source_credits = sources.artist_credits(source.get('artist', ''))
        duration = sources.matching_duration(source.get('duration'))
        if len(source_credits) < 2 or duration is None:
            return candidates
        output = []
        checked = set()
        for candidate in candidates:
            credits = sources.artist_credits(candidate.get('artist', ''))
            seconds = sources.matching_duration(candidate.get('duration'))
            # Only investigate an otherwise matching recording with the same lead.
            # A composer or publisher credit must never stand in for a performer.
            if (not credits or len(credits) >= len(source_credits) or seconds is None
                    or abs(duration - seconds) > 2
                    or sources.normalized_words(source_credits[0]) != sources.normalized_words(credits[0])
                    or not match_track({**source, 'artist': candidate['artist']}, [candidate])[0]):
                output.append(candidate)
                continue
            url = candidate.get('source_url') or candidate.get('download_url') or ''
            identifier = re.fullmatch(r'/(?:browse/)?track/([0-9]+)/?', urlparse(url).path)
            if not identifier or (identifier[1] not in checked and len(checked) >= 5):
                output.append(candidate)
                continue
            checked.add(identifier[1])
            try:
                detail = self.api('tidal', f'tracks/{identifier[1]}/contributors', limit=100)
                guests = [row['name'].strip() for row in detail.get('items', [])
                          if isinstance(row, dict) and isinstance(row.get('name'), str) and row['name'].strip()
                          and sources.normalized_words(row.get('role', '')) in {'featured artist', 'main artist'}]
                if guests:
                    candidate = {**candidate, 'artist': ', '.join(dict.fromkeys([*credits, *guests]))}
            except (MatchError, KeyError, TypeError):
                pass
            output.append(candidate)
        return output

    def album(self, provider, identifier):
        if provider == "tidal":
            meta = self.api(provider, f"albums/{identifier}")
            name, total = meta["title"], meta["numberOfTracks"]
            def page(offset):
                return self.api(provider, f"albums/{identifier}/tracks", offset=offset, limit=100)["items"]
            url = f"https://tidal.com/album/{identifier}"
        elif provider == "beatport":
            meta = self.api(provider, f"releases/{identifier}/")
            name, total = meta["name"], meta["track_count"]
            page_number = 0
            def page(offset):
                nonlocal page_number
                page_number += 1
                return self.api(provider, f"releases/{identifier}/tracks/", page=page_number,
                                per_page=100)["results"]
            url = f"https://www.beatport.com/release/{meta.get('slug', 'release')}/{identifier}"
        else:
            meta = self.api(provider, "album/get", album_id=identifier, limit=100, offset=0)
            name, total = meta["title"], meta["tracks_count"]
            def page(offset):
                data = self.api(provider, "album/get", album_id=identifier, limit=100, offset=offset)
                return [{**t, "album": {k: v for k, v in meta.items() if k != "tracks"}}
                        for t in data["tracks"]["items"]]
            url = f"https://play.qobuz.com/album/{identifier}"
        if type(total) is not int or total < 1:
            raise MatchError("Album vide ou nombre de titres invalide.")
        tracks = []
        while len(tracks) < total:
            items = page(len(tracks))
            if not items or len(tracks) + len(items) > total:
                raise MatchError("Liste des titres de l'album incomplete.")
            tracks.extend(items)
        normalized = self.normalize(provider, tracks)
        if len(normalized) != total:
            raise MatchError("Des titres de l'album sont indisponibles.")
        return {"name": name, "kind": "album", "provider": "tiddl" if provider == "tidal" else provider,
                "source": url, "url": url, "tracks": normalized}

    def search_albums(self, provider, collection):
        name = collection["name"]
        artists = Counter(t.get("artist", "").split(",")[0].strip() for t in collection["tracks"])
        artist, count = artists.most_common(1)[0] if artists else ("", 0)
        queries = [f"{artist} {name}", name] if artist and count > len(collection["tracks"]) / 2 else [name]
        failures = []
        seen = set()
        for query in queries:
            if provider == "tidal":
                items = self.api(provider, "search/albums", query=query, limit=50)["items"]
            elif provider == "beatport":
                items = self.api(provider, "search/", q=query).get("releases", [])
            else:
                items = self.api(provider, "album/search", query=query, limit=50)["albums"]["items"]
            for item in items:
                identifier = str(item["id"])
                if identifier in seen:
                    continue
                seen.add(identifier)
                if sources.normalized_words(item.get("title") or item.get("name", "")) == sources.normalized_words(name):
                    try:
                        yield self.album(provider, identifier)
                    except Exception as exc:
                        failures.append(safe_error(exc))
        if failures:
            raise MatchError("Certains albums candidats sont inaccessibles : " + "; ".join(dict.fromkeys(failures)))

    def track(self, provider, identifier):
        path = {"tidal": f"tracks/{identifier}", "beatport": f"tracks/{identifier}/", "qobuz": "track/get"}[provider]
        raw = self.api(provider, path, **({"track_id": identifier} if provider == "qobuz" else {}))
        tracks = self.normalize(provider, [raw])
        if len(tracks) != 1:
            raise MatchError("Titre indisponible sur la plateforme source.")
        return tracks[0]


def read_input(value, catalog):
    value = value.strip()
    if not value:
        raise MatchError("Entree vide.")
    if not re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        parts = re.split(r"\s+(?:-|\u00b7|\u2013|\u2014)\s+", value, maxsplit=1)
        track = {"artist": parts[0], "title": parts[1]} if len(parts) == 2 else {"artist": "", "title": value}
        return {"name": value, "kind": "track", "provider": "manual", "source": "manual", "tracks": [track]}
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise MatchError("Utilisez un lien HTTPS public de musique.")
    host, path = parsed.hostname, parsed.path.rstrip("/")
    # Native albums and tracks are resolved by ID, never by an arbitrary URL fetch.
    native = {"tidal.com": "tidal", "www.tidal.com": "tidal", "listen.tidal.com": "tidal",
              "beatport.com": "beatport", "www.beatport.com": "beatport",
              "qobuz.com": "qobuz", "www.qobuz.com": "qobuz", "play.qobuz.com": "qobuz", "open.qobuz.com": "qobuz"}
    match = re.fullmatch(r"/(?:browse/|[a-z]{2}(?:-[a-z]{2})?/)?(album|release|track)/(?:[^/]+/)?([A-Za-z0-9]+)", path)
    if host in native and match:
        provider, kind, identifier = native[host], match[1], match[2]
        if provider != "qobuz" and not identifier.isdigit():
            raise MatchError("Identifiant invalide.")
        if kind in {"album", "release"}:
            return catalog.album(provider, identifier)
        track = catalog.track(provider, identifier)
        return {"name": track["title"], "kind": "track", "provider": "tiddl" if provider == "tidal" else provider,
                "source": value, "tracks": [track]}
    try:
        sources.playlist_source(value)
    except sources.PlaylistError:
        track = public_track(value)
        provider = {"open.spotify.com": "spotify", "music.apple.com": "applemusic",
                    "soundcloud.com": "soundcloud", "www.soundcloud.com": "soundcloud"}[host]
        return {"name": track["title"], "kind": "track", "provider": provider, "source": value,
                "tracks": [{**track, "source_url": value}]}
    import export_playlist
    provider, canonical, payload = export_playlist.read_playlist_metadata(value)
    kind = payload.get("kind") or ("album" if "/album/" in path else "playlist")
    return {**payload, "kind": kind, "provider": provider, "source": canonical}


def public_track(url):
    parsed = urlparse(url)
    if parsed.hostname == "open.spotify.com":
        match = re.fullmatch(r"/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{22})/?", parsed.path)
        if match:
            html = sources.get_page(f"https://open.spotify.com/embed/track/{match[1]}").text
            script = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
            entity = json.loads(script.string)["props"]["pageProps"]["state"]["data"]["entity"]
            return {"title": entity["title"], "artist": ", ".join(a["name"] for a in entity["artists"]),
                    "duration": entity["duration"] / 1000}
    if parsed.hostname == "music.apple.com" and re.fullmatch(r"/[a-z]{2}/album/(?:[^/]+/)?[0-9]+/?", parsed.path):
        identifier = parse_qs(parsed.query).get("i", [None])[0]
        if identifier and identifier.isdigit():
            response = sources.get_page(url)
            response.encoding = "utf-8"
            script = BeautifulSoup(response.text, "html.parser").find("script", id="serialized-server-data")
            data = json.loads(script.string)
            for page in data["data"]:
                for section in page.get("data", {}).get("sections", []):
                    if section.get("itemKind") != "trackLockup":
                        continue
                    for track in section["items"]:
                        if track.get("contentDescriptor", {}).get("identifiers", {}).get("storeAdamID") == identifier:
                            return {"title": track["title"], "artist": track["artistName"], "duration": track["duration"] / 1000}
    if parsed.hostname in {"soundcloud.com", "www.soundcloud.com"} and re.fullmatch(r"/[\w-]+/[\w-]+/?", parsed.path):
        from yt_dlp import YoutubeDL
        from yt_dlp.extractor.soundcloud import SoundcloudIE
        class MetadataTrackIE(SoundcloudIE):
            def _extract_info_dict(self, info, full_title=None, secret_token=None, extract_flat=False):
                return sources.soundcloud_metadata({"tracks": [info]}, lambda ids: [])["tracks"][0]
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                        "socket_timeout": 20, "retries": 0, "extractor_retries": 0}) as ydl:
            return MetadataTrackIE(ydl).extract(url)
    raise MatchError("Lien non pris en charge. Utilisez un lien complet de playlist, album ou morceau.")
