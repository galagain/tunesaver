#!/usr/bin/env python3
"""Local web interface for searching TIDAL, Beatport and Qobuz."""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse

import requests
from aiohttp import web

import musicdl


LOGGER = logging.getLogger("musicdl.web")

ASSET_ROOT = Path(os.environ.get("MUSICDL_WEB_ASSET_DIR", "/app/web"))
TIDAL_AUTH_PATH = musicdl.STATE_ROOT / "tiddl" / "auth.json"
BEATPORT_CACHE_PATH = (
    musicdl.STATE_ROOT / "beatportdl" / "beatportdl-credentials.json"
)
BEATPORT_CONFIG_PATH = Path(
    os.environ.get("MUSICDL_BEATPORT_CONFIG", "/config/beatport.json")
)
BEATPORT_SEARCH_BINARY = os.environ.get(
    "MUSICDL_BEATPORT_SEARCH_BINARY", "/usr/local/bin/beatport-search"
)
QOBUZ_SEARCH_SCRIPT = os.environ.get(
    "MUSICDL_QOBUZ_SEARCH_SCRIPT", "/app/qobuz_search.py"
)

TIDAL_SEARCH_URL = "https://api.tidal.com/v1/search"
BEATPORT_SEARCH_URL = "https://api.beatport.com/v4/catalog/search/"
RESULT_LIMIT = 8
SEARCH_TEXT_LIMIT = 200
DIRECT_LINK_LIMIT = 2048
JSON_BODY_LIMIT = 131072
STATUS_CACHE_SECONDS = 3
AUTH_CACHE_SECONDS = 60
DOWNLOAD_TICKET_SECONDS = 120
MAX_BATCH_LINKS = 50
ALLOWED_HOSTS = {
    host.strip().lower()
    for host in os.environ.get(
        "TUNESAVER_ALLOWED_HOSTS", "127.0.0.1,localhost,::1"
    ).split(",")
    if host.strip()
}
SUPABASE_URL = os.environ.get("TUNESAVER_SUPABASE_URL", "").rstrip("/")
SUPABASE_PUBLISHABLE_KEY = os.environ.get(
    "TUNESAVER_SUPABASE_PUBLISHABLE_KEY", ""
)

PLATFORM_NAMES = {"tiddl": "TIDAL", "beatport": "Beatport", "qobuz": "Qobuz"}
KIND_NAMES = {
    "track": "Morceau",
    "video": "Vidéo",
    "album": "Album",
    "playlist": "Playlist",
    "artist": "Artiste",
    "mix": "Mix",
    "release": "Release",
    "chart": "Chart",
    "label": "Label",
}
SEARCH_SEMAPHORES_KEY = web.AppKey("search_semaphores", dict)
STATUS_CACHE_KEY = web.AppKey("status_cache", dict)
AUTH_CACHE_KEY = web.AppKey("auth_cache", dict)


class PublicError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class SourceError(PublicError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status=503)


def _security_headers(response: web.StreamResponse, request: web.Request) -> None:
    connect_sources = ["'self'"]
    if SUPABASE_URL:
        parsed_supabase = urlparse(SUPABASE_URL)
        if parsed_supabase.scheme == "https" and parsed_supabase.netloc:
            connect_sources.append(f"{parsed_supabase.scheme}://{parsed_supabase.netloc}")
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; base-uri 'none'; connect-src {' '.join(connect_sources)}; "
        "font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
        "img-src 'self'; object-src 'none'; script-src 'self'; style-src 'self'"
    )
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    else:
        response.headers.setdefault("Cache-Control", "no-cache")


def _host_parts(value: str) -> tuple[str, int | None]:
    try:
        parsed = urlparse(f"//{value}")
        port = parsed.port
    except ValueError as exc:
        raise PublicError("Hôte local invalide.", status=403) from exc
    hostname = (parsed.hostname or "").lower()
    if (
        hostname not in ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
    ):
        raise PublicError("Ce nom d'hôte n'est pas autorisé.", status=403)
    return hostname, port


def _check_local_host(request: web.Request) -> tuple[str, int | None]:
    host = request.headers.get("Host", "")
    if not host:
        raise PublicError("En-tête Host manquant.", status=403)
    return _host_parts(host)


def _check_same_origin(request: web.Request) -> None:
    expected_host = _check_local_host(request)
    fetch_site = request.headers.get("Sec-Fetch-Site", "")
    if fetch_site == "cross-site":
        raise PublicError("Requête intersite refusée.", status=403)
    origin = request.headers.get("Origin")
    if not origin:
        return
    parsed = urlparse(origin)
    try:
        origin_port = parsed.port
    except ValueError as exc:
        raise PublicError("Origine refusée.", status=403) from exc
    origin_host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or (origin_host, origin_port) != expected_host
    ):
        raise PublicError("Origine refusée.", status=403)


def verify_supabase_token(token: str) -> dict[str, str] | None:
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        raise RuntimeError("Supabase n'est pas configuré")
    try:
        response = requests.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "apikey": SUPABASE_PUBLISHABLE_KEY,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=10,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise RuntimeError("Supabase est momentanément indisponible") from exc
    if response.status_code in {401, 403}:
        return None
    if response.status_code != 200:
        raise RuntimeError("Supabase a refusé la vérification")
    try:
        payload = response.json()
        user_id = str(uuid.UUID(str(payload["id"])))
    except (KeyError, TypeError, ValueError, requests.JSONDecodeError) as exc:
        raise RuntimeError("Réponse d'authentification Supabase invalide") from exc
    email = payload.get("email")
    try:
        profile_response = requests.get(
            f"{SUPABASE_URL}/rest/v1/profiles",
            params={"select": "approved", "user_id": f"eq.{user_id}"},
            headers={
                "apikey": SUPABASE_PUBLISHABLE_KEY,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=10,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise RuntimeError("Impossible de vérifier l'autorisation utilisateur") from exc
    if profile_response.status_code != 200:
        raise RuntimeError("La table d'autorisation Supabase n'est pas prête")
    try:
        profiles = profile_response.json()
    except requests.JSONDecodeError as exc:
        raise RuntimeError("Réponse d'autorisation Supabase invalide") from exc
    approved = (
        isinstance(profiles, list)
        and len(profiles) == 1
        and isinstance(profiles[0], dict)
        and profiles[0].get("approved") is True
    )
    return {
        "id": user_id,
        "email": email if isinstance(email, str) else "",
        "approved": approved,
    }


async def authenticate_request(request: web.Request) -> dict[str, str]:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise PublicError("Connexion requise.", status=401)
    token = authorization[7:].strip()
    if not token or len(token) > 8192:
        raise PublicError("Session invalide.", status=401)
    cache_key = hashlib.sha256(token.encode()).hexdigest()
    cache = request.app[AUTH_CACHE_KEY]
    cached = cache.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    try:
        user = await asyncio.to_thread(verify_supabase_token, token)
    except RuntimeError as exc:
        raise SourceError(str(exc)) from exc
    if user is None:
        raise PublicError("Session expirée. Reconnectez-vous.", status=401)
    if not user.get("approved"):
        raise PublicError("Compte en attente d'autorisation.", status=403)
    cache[cache_key] = (time.monotonic() + AUTH_CACHE_SECONDS, user)
    return user


@web.middleware
async def api_middleware(
    request: web.Request, handler: Callable[[web.Request], Any]
) -> web.StreamResponse:
    try:
        _check_local_host(request)
        public_paths = {"/", "/app.js", "/style.css", "/api/health", "/api/config"}
        if request.path not in public_paths and not request.path.startswith("/api/files/"):
            request["user"] = await authenticate_request(request)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            _check_same_origin(request)
            if request.content_type != "application/json":
                raise PublicError("Le corps doit être en JSON.", status=415)
        response = await handler(request)
    except PublicError as exc:
        response = web.json_response({"error": exc.message}, status=exc.status)
    except web.HTTPException as exc:
        response = exc
    except asyncio.TimeoutError:
        response = web.json_response(
            {"error": "La plateforme a mis trop de temps à répondre."}, status=504
        )
    except Exception:
        LOGGER.exception("erreur web non gérée")
        response = web.json_response(
            {"error": "Erreur interne de l'interface."}, status=500
        )
    _security_headers(response, request)
    return response


async def read_json_object(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json(loads=json.loads)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PublicError("JSON invalide.") from exc
    if not isinstance(payload, dict):
        raise PublicError("Un objet JSON est attendu.")
    return payload


def normalized_query(value: Any) -> str:
    if not isinstance(value, str):
        raise PublicError("Saisissez un nom ou un lien.")
    query = value.strip()
    if not query:
        raise PublicError("Saisissez un nom ou un lien.")
    if "\x00" in query or len(query) > DIRECT_LINK_LIMIT:
        raise PublicError("La recherche est trop longue.")
    short_tidal = re.fullmatch(
        r"(track|video|album|artist)/([0-9]+)|(playlist|mix)/([A-Za-z0-9-]+)",
        query,
        flags=re.IGNORECASE,
    )
    if short_tidal:
        kind = (short_tidal.group(1) or short_tidal.group(3)).lower()
        resource_id = short_tidal.group(2) or short_tidal.group(4)
        return f"{kind}/{resource_id}"
    if query[:8].lower() == "https://":
        return f"https://{query[8:]}"
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", query):
        return query
    query = re.sub(r"\s+", " ", query)
    if len(query) < 2:
        raise PublicError("Saisissez au moins deux caractères.")
    if len(query) > SEARCH_TEXT_LIMIT:
        raise PublicError(f"Limitez la recherche à {SEARCH_TEXT_LIMIT} caractères.")
    return query


def resource_kind(provider: str, value: str) -> str:
    if provider == "tiddl" and musicdl.TIDAL_RESOURCE_RE.fullmatch(value):
        return value.split("/", 1)[0]
    segments = [segment for segment in urlparse(value).path.split("/") if segment]
    if provider == "tiddl":
        if segments and segments[0] == "browse":
            segments = segments[1:]
        return segments[0] if segments else "track"

    if provider == "qobuz":
        if segments and re.fullmatch(r"[a-z]{2}-[a-z]{2}", segments[0]):
            segments = segments[1:]
        return segments[0] if segments else "track"

    if len(segments) > 1 and len(segments[0]) == 2:
        segments = segments[1:]
        if segments and segments[0] == "catalog":
            segments = segments[1:]
    if not segments:
        return "track"
    aliases = {"tracks": "track", "releases": "release", "playlist": "chart"}
    return aliases.get(segments[0], segments[0])


def direct_result(value: str) -> dict[str, Any] | None:
    for provider in musicdl.PROVIDERS:
        try:
            download_url = musicdl.validate_url(provider, value)
        except ValueError:
            continue
        kind = resource_kind(provider, download_url)
        if provider == "tiddl" and not download_url.startswith("https://"):
            source_url = f"https://listen.tidal.com/{download_url}"
        else:
            source_url = download_url
        digest = hashlib.sha256(f"{provider}:{download_url}".encode()).hexdigest()[:16]
        platform = PLATFORM_NAMES[provider]
        return {
            "id": f"{provider}:direct:{digest}",
            "provider": provider,
            "platform": platform,
            "kind": kind,
            "title": f"Lien {platform}",
            "artist": "",
            "details": f"{KIND_NAMES.get(kind, kind.title())} direct",
            "source_url": source_url,
            "download_url": download_url,
            "requires_confirmation": kind not in {"track", "video"},
        }
    return None


def format_duration(seconds: Any) -> str:
    try:
        value = max(int(seconds), 0)
    except (TypeError, ValueError):
        return ""
    minutes, remaining = divmod(value, 60)
    return f"{minutes}:{remaining:02d}"


def normalize_tidal_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tracks_block = payload.get("tracks")
    if tracks_block is None:
        tracks_block = {}
    if not isinstance(tracks_block, dict):
        raise SourceError("La réponse TIDAL est invalide.")
    tracks = tracks_block.get("items", [])
    if not isinstance(tracks, list):
        raise SourceError("La réponse TIDAL est invalide.")

    candidates: list[Any] = []
    top_hit = payload.get("topHit")
    if (
        isinstance(top_hit, dict)
        and top_hit.get("type") == "TRACKS"
        and isinstance(top_hit.get("value"), dict)
    ):
        candidates.append(top_hit["value"])
    candidates.extend(tracks)

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for track in candidates:
        if not isinstance(track, dict):
            continue
        track_id = str(track.get("id", ""))
        title = str(track.get("title", "")).strip()
        if not track_id.isdigit() or not title or track_id in seen:
            continue
        if track.get("allowStreaming") is not True or track.get("streamReady") is not True:
            continue

        artists = track.get("artists", [])
        if not isinstance(artists, list):
            artists = []
        artist_names = [
            str(artist.get("name", "")).strip()
            for artist in artists
            if isinstance(artist, dict) and artist.get("name")
        ]
        artist = ", ".join(artist_names)
        album = track.get("album") if isinstance(track.get("album"), dict) else {}
        details = [str(album.get("title", "")).strip()]
        quality = str(track.get("audioQuality", "")).replace("_", " ").strip()
        if quality:
            details.append(quality)
        duration = format_duration(track.get("duration"))
        if duration:
            details.append(duration)
        download_url = f"https://listen.tidal.com/track/{track_id}"
        normalized.append(
            {
                "id": f"tiddl:track:{track_id}",
                "provider": "tiddl",
                "platform": "TIDAL",
                "kind": "track",
                "title": title,
                "artist": artist,
                "details": " · ".join(part for part in details if part),
                "source_url": download_url,
                "download_url": download_url,
            }
        )
        seen.add(track_id)
        if len(normalized) >= RESULT_LIMIT:
            break
    return normalized


def normalize_beatport_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = payload.get("tracks", [])
    if not isinstance(tracks, list):
        raise SourceError("La réponse Beatport est invalide.")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for track in tracks:
        if not isinstance(track, dict):
            continue
        track_id = str(track.get("id", ""))
        title = str(track.get("name", "")).strip()
        slug = str(track.get("slug", "")).strip() or "track"
        if not track_id.isdigit() or not title or track_id in seen:
            continue

        artists = track.get("artists", [])
        artist_names = [
            str(artist.get("name", "")).strip()
            for artist in artists
            if isinstance(artist, dict) and artist.get("name")
        ]
        details: list[str] = []
        mix_name = str(track.get("mix_name", "")).strip()
        if mix_name and mix_name.lower() != "original mix":
            details.append(mix_name)
        release = track.get("release") if isinstance(track.get("release"), dict) else {}
        if release.get("name"):
            details.append(str(release["name"]).strip())
        bpm = track.get("bpm")
        if isinstance(bpm, (int, float)) and bpm:
            details.append(f"{int(bpm)} BPM")
        key = track.get("key") if isinstance(track.get("key"), dict) else {}
        if key.get("name"):
            details.append(str(key["name"]).strip())

        safe_slug = quote(slug, safe="-")
        download_url = f"https://www.beatport.com/track/{safe_slug}/{track_id}"
        normalized.append(
            {
                "id": f"beatport:track:{track_id}",
                "provider": "beatport",
                "platform": "Beatport",
                "kind": "track",
                "title": title,
                "artist": ", ".join(artist_names),
                "details": " · ".join(part for part in details if part),
                "source_url": download_url,
                "download_url": download_url,
            }
        )
        seen.add(track_id)
        if len(normalized) >= RESULT_LIMIT:
            break
    return normalized


def normalize_qobuz_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tracks_block = payload.get("tracks", {})
    if not isinstance(tracks_block, dict) or not isinstance(tracks_block.get("items", []), list):
        raise SourceError("La réponse Qobuz est invalide.")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for track in tracks_block.get("items", []):
        if not isinstance(track, dict):
            continue
        track_id = str(track.get("id", "")).strip()
        title = str(track.get("title", "")).strip()
        if not track_id or not title or track_id in seen or track.get("streamable") is False:
            continue
        performer = track.get("performer") if isinstance(track.get("performer"), dict) else {}
        artist = str(performer.get("name", "")).strip()
        album = track.get("album") if isinstance(track.get("album"), dict) else {}
        details = [str(album.get("title", "")).strip()]
        if track.get("hires_streamable") is True:
            bit_depth = track.get("maximum_bit_depth")
            sampling_rate = track.get("maximum_sampling_rate")
            if bit_depth and sampling_rate:
                details.append(f"Hi-Res {bit_depth}-bit/{sampling_rate} kHz")
            else:
                details.append("Hi-Res")
        duration = format_duration(track.get("duration"))
        if duration:
            details.append(duration)
        download_url = f"https://play.qobuz.com/track/{quote(track_id, safe='')}"
        normalized.append(
            {
                "id": f"qobuz:track:{track_id}",
                "provider": "qobuz",
                "platform": "Qobuz",
                "kind": "track",
                "title": title,
                "artist": artist,
                "details": " · ".join(part for part in details if part),
                "source_url": download_url,
                "download_url": download_url,
            }
        )
        seen.add(track_id)
        if len(normalized) >= RESULT_LIMIT:
            break
    return normalized


def load_json_file(path: Path, description: str) -> dict[str, Any]:
    payload: Any = None
    for attempt in range(3):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            break
        except json.JSONDecodeError as exc:
            if attempt == 2:
                raise SourceError(f"{description} invalide.") from exc
            time.sleep(0.05)
        except OSError as exc:
            raise SourceError(f"{description} indisponible.") from exc
    if not isinstance(payload, dict):
        raise SourceError(f"{description} invalide.")
    return payload


def tidal_auth_valid(auth_data: dict[str, Any], *, margin: int = 60) -> bool:
    try:
        expires_at = int(auth_data.get("expires_at", 0))
    except (TypeError, ValueError):
        return False
    required = ("token", "refresh_token", "user_id", "country_code")
    return all(auth_data.get(key) for key in required) and expires_at > time.time() + margin


def refresh_tidal_auth(
    *, force: bool, rejected_token: str | None = None
) -> dict[str, Any]:
    try:
        lock = musicdl.provider_lock("tiddl", "operation", blocking=False)
        with lock:
            auth_data = load_json_file(TIDAL_AUTH_PATH, "Authentification TIDAL")
            if not force and tidal_auth_valid(auth_data):
                return auth_data
            if (
                force
                and rejected_token
                and auth_data.get("token") != rejected_token
                and tidal_auth_valid(auth_data)
            ):
                return auth_data

            refresh_token = auth_data.get("refresh_token")
            if not isinstance(refresh_token, str) or not refresh_token:
                raise SourceError(
                    "TIDAL n'est pas connecté. Lancez ./musicdl auth tiddl."
                )
            from tiddl.core.auth.client import AUTH_URL, get_auth_credentials

            client_id, client_secret = get_auth_credentials()
            response = requests.post(
                f"{AUTH_URL}/token",
                data={
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "scope": "r_usr+w_usr+w_sub",
                },
                auth=(client_id, client_secret),
                timeout=(5, 20),
            )
            if response.status_code == 429:
                raise SourceError("TIDAL limite temporairement les connexions.")
            if response.status_code >= 500:
                raise SourceError("Le service de connexion TIDAL est indisponible.")
            if response.status_code != 200:
                raise SourceError(
                    "La session TIDAL a expiré. Lancez ./musicdl auth tiddl --force."
                )
            try:
                refreshed = response.json()
            except (requests.exceptions.JSONDecodeError, json.JSONDecodeError) as exc:
                raise SourceError("La réponse de connexion TIDAL est invalide.") from exc
            if not isinstance(refreshed, dict):
                raise SourceError("La réponse de connexion TIDAL est invalide.")
            access_token = refreshed.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise SourceError("TIDAL n'a pas renvoyé de nouveau jeton.")
            try:
                expires_in = int(refreshed.get("expires_in"))
            except (TypeError, ValueError) as exc:
                raise SourceError("La durée du jeton TIDAL est invalide.") from exc
            if not 1 <= expires_in <= 31_536_000:
                raise SourceError("La durée du jeton TIDAL est invalide.")
            auth_data["token"] = access_token
            new_refresh_token = refreshed.get("refresh_token")
            if isinstance(new_refresh_token, str) and new_refresh_token:
                auth_data["refresh_token"] = new_refresh_token
            auth_data["expires_at"] = int(time.time()) + expires_in
            musicdl.atomic_write_json(TIDAL_AUTH_PATH, auth_data)
            return auth_data
    except RuntimeError as exc:
        raise SourceError(
            "TIDAL est occupé par un téléchargement; réessayez dans un instant."
        ) from exc
    except (requests.RequestException, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SourceError("Impossible de rafraîchir la session TIDAL.") from exc


def current_tidal_auth() -> dict[str, Any]:
    auth_data = load_json_file(TIDAL_AUTH_PATH, "Authentification TIDAL")
    if tidal_auth_valid(auth_data):
        return auth_data
    return refresh_tidal_auth(force=False)


def search_tidal(query: str) -> list[dict[str, Any]]:
    auth_data = current_tidal_auth()

    def request_search(token: str) -> requests.Response:
        return requests.get(
            TIDAL_SEARCH_URL,
            params={"countryCode": auth_data["country_code"], "query": query},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=(5, 20),
        )

    try:
        rejected_token = str(auth_data["token"])
        response = request_search(rejected_token)
        if response.status_code == 401:
            auth_data = refresh_tidal_auth(
                force=True, rejected_token=rejected_token
            )
            response = request_search(str(auth_data["token"]))
        if response.status_code == 429:
            raise SourceError("TIDAL limite temporairement les recherches.")
        if response.status_code != 200:
            raise SourceError(f"TIDAL a répondu avec le code {response.status_code}.")
        try:
            payload = response.json()
        except (requests.exceptions.JSONDecodeError, json.JSONDecodeError) as exc:
            raise SourceError("La réponse TIDAL est invalide.") from exc
    except SourceError:
        raise
    except requests.RequestException as exc:
        raise SourceError("TIDAL est momentanément inaccessible.") from exc
    except json.JSONDecodeError as exc:
        raise SourceError("La réponse TIDAL est invalide.") from exc
    if not isinstance(payload, dict):
        raise SourceError("La réponse TIDAL est invalide.")
    return normalize_tidal_results(payload)


def fnv1a_login_id(username: str, password: str) -> str:
    value = 14695981039346656037
    for byte in f"{username}:{password}".encode():
        value ^= byte
        value = (value * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


def valid_beatport_token() -> str | None:
    try:
        username = musicdl.read_secret("beatport_username")
        password = musicdl.read_secret("beatport_password")
        cache = load_json_file(BEATPORT_CACHE_PATH, "Cache Beatport")
        expires_at = int(cache.get("issued_at", 0)) + int(cache.get("expires_in", 0))
        token = cache.get("access_token")
        if (
            isinstance(token, str)
            and token
            and cache.get("login_id") == fnv1a_login_id(username, password)
            and expires_at > time.time() + 360
        ):
            return token
    except (OSError, RuntimeError, SourceError, TypeError, ValueError):
        return None
    return None


def beatport_api_search(query: str, token: str) -> dict[str, Any] | None:
    try:
        response = requests.get(
            BEATPORT_SEARCH_URL,
            params={
                "q": query,
                "order_by": "-publish_date",
                "is_available_for_streaming": "true",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8,"
                    "application/signed-exchange;v=b3;q=0.7"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "max-age=0",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
            },
            timeout=(5, 20),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise SourceError("Beatport est momentanément inaccessible.") from exc
    if response.status_code in {401, 403}:
        return None
    if response.status_code == 429:
        raise SourceError("Beatport limite temporairement les recherches.")
    if response.status_code != 200:
        raise SourceError(f"Beatport a répondu avec le code {response.status_code}.")
    try:
        payload = response.json()
    except (requests.exceptions.JSONDecodeError, json.JSONDecodeError) as exc:
        raise SourceError("La réponse Beatport est invalide.") from exc
    if not isinstance(payload, dict):
        raise SourceError("La réponse Beatport est invalide.")
    return payload


def configured_beatport_proxy() -> str:
    try:
        settings = json.loads(BEATPORT_CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceError("La configuration Beatport est invalide.") from exc
    if not isinstance(settings, dict):
        raise SourceError("La configuration Beatport est invalide.")
    proxy = settings.get("proxy", "")
    if proxy is None:
        return ""
    if not isinstance(proxy, str):
        raise SourceError("Le proxy Beatport configuré est invalide.")
    return proxy.strip()


def beatport_helper_search(query: str, *, proxy: str = "") -> dict[str, Any]:
    command = [BEATPORT_SEARCH_BINARY]
    if proxy:
        command.extend(["--proxy", proxy])
    command.extend(["--", query])
    try:
        with musicdl.provider_lock("beatport", "operation", blocking=False):
            process = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=210,
                check=False,
                env=musicdl.runtime_environment(),
            )
    except RuntimeError as exc:
        raise SourceError(
            "Beatport est occupé par un téléchargement; réessayez dans un instant."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError("Beatport a mis trop de temps à répondre.") from exc
    except OSError as exc:
        raise SourceError("Le module de recherche Beatport est indisponible.") from exc

    prefix = "MUSICDL_JSON:"
    payload_line = next(
        (line[len(prefix) :] for line in reversed(process.stdout.splitlines()) if line.startswith(prefix)),
        None,
    )
    if process.returncode != 0 or payload_line is None:
        LOGGER.warning("beatport-search a échoué avec le code %s", process.returncode)
        raise SourceError(
            "Connexion ou recherche Beatport impossible. Vérifiez vos identifiants."
        )
    try:
        payload = json.loads(payload_line)
    except json.JSONDecodeError as exc:
        raise SourceError("La réponse Beatport est invalide.") from exc
    if not isinstance(payload, dict):
        raise SourceError("La réponse Beatport est invalide.")
    return payload


def search_beatport(query: str) -> list[dict[str, Any]]:
    proxy = configured_beatport_proxy()
    token = valid_beatport_token() if not proxy else None
    payload = beatport_api_search(query, token) if token else None
    if payload is None:
        payload = beatport_helper_search(query, proxy=proxy)
    return normalize_beatport_results(payload)


def search_qobuz(query: str) -> list[dict[str, Any]]:
    try:
        with musicdl.provider_lock("qobuz", "operation", blocking=False):
            process = subprocess.run(
                [os.environ.get("PYTHON", "python"), QOBUZ_SEARCH_SCRIPT, query],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=75,
                check=False,
                env=musicdl.runtime_environment("qobuz"),
            )
    except RuntimeError as exc:
        raise SourceError(
            "Qobuz est occupé par un téléchargement; réessayez dans un instant."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError("Qobuz a mis trop de temps à répondre.") from exc
    except OSError as exc:
        raise SourceError("Le module de recherche Qobuz est indisponible.") from exc

    prefix = "MUSICDL_JSON:"
    payload_line = next(
        (
            line[len(prefix) :]
            for line in reversed(process.stdout.splitlines())
            if line.startswith(prefix)
        ),
        None,
    )
    if process.returncode != 0 or payload_line is None:
        LOGGER.warning("qobuz-search a échoué avec le code %s", process.returncode)
        raise SourceError(
            "Connexion ou recherche Qobuz impossible. Lancez ./musicdl auth qobuz --force."
        )
    try:
        payload = json.loads(payload_line)
    except json.JSONDecodeError as exc:
        raise SourceError("La réponse Qobuz est invalide.") from exc
    if not isinstance(payload, dict):
        raise SourceError("La réponse Qobuz est invalide.")
    return normalize_qobuz_results(payload)


async def execute_search(request: web.Request, provider: str, query: str) -> list[dict[str, Any]]:
    functions = {"tiddl": search_tidal, "beatport": search_beatport, "qobuz": search_qobuz}
    semaphore: asyncio.Semaphore = request.app[SEARCH_SEMAPHORES_KEY][provider]
    async with semaphore:
        return await asyncio.to_thread(functions[provider], query)


async def search_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    query = normalized_query(payload.get("query"))
    provider = payload.get("provider")
    if provider is not None and provider not in musicdl.PROVIDERS:
        raise PublicError("Plateforme inconnue.")

    direct = direct_result(query)
    if direct is not None:
        if provider is not None and direct["provider"] != provider:
            return web.json_response({"query": query, "results": [], "errors": {}})
        return web.json_response({"query": query, "results": [direct], "errors": {}})

    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", query):
        raise PublicError(
            "Seuls les liens directs TIDAL, Beatport et Qobuz pris en charge sont acceptés."
        )

    if provider is not None:
        results = await execute_search(request, provider, query)
        return web.json_response({"query": query, "results": results, "errors": {}})

    tasks = [
        asyncio.create_task(execute_search(request, item_provider, query))
        for item_provider in musicdl.PROVIDERS
    ]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for item_provider, outcome in zip(musicdl.PROVIDERS, outcomes, strict=True):
        if isinstance(outcome, PublicError):
            errors[item_provider] = outcome.message
        elif isinstance(outcome, asyncio.TimeoutError):
            errors[item_provider] = "La plateforme a mis trop de temps à répondre."
        elif isinstance(outcome, BaseException):
            LOGGER.exception(
                "recherche %s non gérée",
                item_provider,
                exc_info=(type(outcome), outcome, outcome.__traceback__),
            )
            errors[item_provider] = "Erreur interne pendant la recherche."
        else:
            results.extend(outcome)
    return web.json_response({"query": query, "results": results, "errors": errors})


def existing_job(provider: str, url: str, owner_id: str | None = None) -> str | None:
    for state in ("pending", "running"):
        directory = musicdl.QUEUE_ROOT / state / provider
        for path in directory.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                if owner_id is not None and payload.get("owner_id") != owner_id:
                    continue
                urls = payload.get("urls", [])
                if isinstance(urls, list) and url in urls:
                    return str(payload.get("id") or path.stem)
    return None


def worker_is_active(provider: str) -> bool:
    try:
        handle = musicdl.acquire_provider_lock(provider, "worker", blocking=False)
    except RuntimeError:
        return True
    musicdl.release_provider_lock(handle)
    return False


def display_metadata(payload: dict[str, Any], kind: str) -> dict[str, str]:
    metadata: dict[str, str] = {"kind": kind}
    for key in ("title", "artist"):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise PublicError("Les informations du morceau sont invalides.")
        value = re.sub(r"\s+", " ", value.strip())
        if len(value) > 200:
            raise PublicError("Les informations du morceau sont trop longues.")
        if value:
            metadata[key] = value
    return metadata


async def download_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    provider = payload.get("provider")
    value = payload.get("url")
    if provider not in musicdl.PROVIDERS or not isinstance(value, str):
        raise PublicError("Téléchargement invalide.")
    if len(value) > DIRECT_LINK_LIMIT:
        raise PublicError("Le lien est trop long.")
    try:
        url = musicdl.validate_url(provider, value)
    except ValueError as exc:
        raise PublicError("Le lien n'appartient pas à la plateforme choisie.") from exc
    kind = resource_kind(provider, url)
    if kind in {"artist", "label"}:
        raise PublicError(
            "Le téléchargement complet d'un artiste ou d'un label est désactivé "
            "dans l'interface web."
        )
    if kind not in {"track", "video"} and payload.get("confirm_complete") is not True:
        raise PublicError(
            "Confirmez explicitement le téléchargement de cette ressource complète."
        )
    if not worker_is_active(provider):
        raise SourceError(
            f"Le worker {PLATFORM_NAMES[provider]} n'est pas démarré. "
            f"Lancez ./musicdl start {provider}."
        )
    metadata = display_metadata(payload, kind)
    owner_id = request["user"]["id"]

    with musicdl.provider_lock(provider, "enqueue"):
        job_id = existing_job(provider, url, owner_id)
        duplicate = job_id is not None
        if job_id is None:
            job_id = musicdl.enqueue(
                provider,
                [url],
                metadata=metadata,
                owner_id=owner_id,
                ephemeral=True,
            )
    return web.json_response(
        {"job_id": job_id, "duplicate": duplicate}, status=200 if duplicate else 202
    )


async def batch_download_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    links = payload.get("links")
    if not isinstance(links, list) or not links or len(links) > MAX_BATCH_LINKS:
        raise PublicError(f"Ajoutez entre 1 et {MAX_BATCH_LINKS} liens.")
    owner_id = request["user"]["id"]
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in links:
        if not isinstance(value, str) or len(value) > DIRECT_LINK_LIMIT:
            raise PublicError("La liste contient un lien invalide.")
        direct = direct_result(value.strip())
        if direct is None:
            raise PublicError("Chaque ligne doit être un lien TIDAL, Beatport ou Qobuz.")
        if direct["download_url"] in seen:
            continue
        seen.add(direct["download_url"])
        if direct["kind"] in {"artist", "label"}:
            raise PublicError("Les liens d'artiste et de label ne sont pas acceptés.")
        if direct["kind"] not in {"track", "video"} and payload.get("confirm_complete") is not True:
            raise PublicError("Confirmez le téléchargement des ressources complètes.")
        if not worker_is_active(direct["provider"]):
            raise SourceError(f"Le worker {direct['platform']} n'est pas démarré.")
        prepared.append(direct)

    jobs: list[dict[str, Any]] = []
    for direct in prepared:
        provider = direct["provider"]
        url = direct["download_url"]
        with musicdl.provider_lock(provider, "enqueue"):
            job_id = existing_job(provider, url, owner_id)
            duplicate = job_id is not None
            if job_id is None:
                job_id = musicdl.enqueue(
                    provider,
                    [url],
                    metadata={
                        "title": direct["title"],
                        "artist": direct["artist"],
                        "kind": direct["kind"],
                    },
                    owner_id=owner_id,
                    ephemeral=True,
                )
        jobs.append({"job_id": job_id, "duplicate": duplicate, "provider": provider})
    return web.json_response({"jobs": jobs}, status=202)


def queue_snapshot(owner_id: str) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    newest: list[tuple[float, str, str, str]] = []
    for provider in musicdl.PROVIDERS:
        counts[provider] = {}
        for state in musicdl.STATES:
            directory = musicdl.QUEUE_ROOT / state / provider
            count = 0
            try:
                entries = os.scandir(directory)
            except OSError:
                counts[provider][state] = 0
                continue
            with entries:
                for entry in entries:
                    if not entry.name.endswith(".json") or not entry.is_file():
                        continue
                    try:
                        payload = json.loads(Path(entry.path).read_text(encoding="utf-8"))
                        if not isinstance(payload, dict) or payload.get("owner_id") != owner_id:
                            continue
                        count += 1
                        candidate = (
                            entry.stat().st_mtime,
                            entry.path,
                            provider,
                            state,
                        )
                    except (OSError, json.JSONDecodeError):
                        continue
                    if len(newest) < 1000:
                        heapq.heappush(newest, candidate)
                    else:
                        heapq.heappushpop(newest, candidate)
            counts[provider][state] = count

    recent: list[dict[str, Any]] = []
    for _mtime, path_string, provider, state in sorted(newest, reverse=True):
        path = Path(path_string)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            display = payload.get("display")
            if not isinstance(display, dict):
                display = {}
            item = {
                "id": str(payload.get("id") or path.stem),
                "provider": provider,
                "state": state,
                "attempts": int(payload.get("attempts", 0)),
                "title": str(display.get("title", ""))[:200],
                "artist": str(display.get("artist", ""))[:200],
                "kind": str(display.get("kind", ""))[:32],
                "source_url": str((payload.get("urls") or [""])[0])[:DIRECT_LINK_LIMIT],
                "created_at": str(payload.get("created_at", ""))[:40],
                "completed_at": str(payload.get("last_finished_at", ""))[:40],
                "delivered_at": str(payload.get("delivered_at", ""))[:40],
                "track_count": int(payload.get("track_count", 0)),
                "estimated_savings_cents": int(
                    payload.get("estimated_savings_cents", 0)
                ),
                "download_ready": state == "done"
                and isinstance(payload.get("delivery"), dict),
            }
            if state == "failed" and payload.get("last_error"):
                item["error"] = str(payload["last_error"])[:240]
            recent.append(item)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return {
        "counts": counts,
        "active": {
            provider: worker_is_active(provider) for provider in musicdl.PROVIDERS
        },
        "recent": recent,
    }


async def status_handler(request: web.Request) -> web.Response:
    snapshot = await asyncio.to_thread(queue_snapshot, request["user"]["id"])
    return web.json_response(snapshot)


def find_done_job(job_id: str) -> tuple[Path, dict[str, Any]]:
    if not musicdl.JOB_ID_RE.fullmatch(job_id):
        raise PublicError("Téléchargement invalide.", status=404)
    for provider in musicdl.PROVIDERS:
        path = musicdl.QUEUE_ROOT / "done" / provider / f"{job_id}.json"
        if path.is_file():
            try:
                return path, musicdl.load_job(path, expected_provider=provider)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise PublicError("Téléchargement invalide.", status=404) from exc
    raise PublicError("Téléchargement introuvable.", status=404)


async def ticket_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    job_id = payload.get("job_id")
    if not isinstance(job_id, str):
        raise PublicError("Téléchargement invalide.")
    path, job = find_done_job(job_id)
    if job.get("owner_id") != request["user"]["id"]:
        raise PublicError("Téléchargement introuvable.", status=404)
    delivery = job.get("delivery")
    if not isinstance(delivery, dict):
        raise PublicError("Ce fichier a déjà été récupéré.", status=410)
    token = secrets.token_urlsafe(32)
    job["download_ticket_hash"] = hashlib.sha256(token.encode()).hexdigest()
    job["download_ticket_expires"] = int(time.time()) + DOWNLOAD_TICKET_SECONDS
    musicdl.atomic_write_json(path, job)
    return web.json_response({"url": f"/api/files/{job_id}?ticket={quote(token)}"})


async def file_handler(request: web.Request) -> web.StreamResponse:
    job_id = request.match_info.get("job_id", "")
    ticket = request.query.get("ticket", "")
    path, job = find_done_job(job_id)
    expected = str(job.get("download_ticket_hash", ""))
    expires = int(job.get("download_ticket_expires", 0))
    actual = hashlib.sha256(ticket.encode()).hexdigest() if ticket else ""
    if (
        not expected
        or expires < int(time.time())
        or not secrets.compare_digest(expected, actual)
    ):
        raise PublicError("Lien de téléchargement invalide ou expiré.", status=403)
    delivery = job.get("delivery")
    if not isinstance(delivery, dict) or not isinstance(delivery.get("path"), str):
        raise PublicError("Ce fichier a déjà été récupéré.", status=410)

    candidate = (musicdl.DELIVERY_ROOT / delivery["path"]).resolve()
    root = musicdl.DELIVERY_ROOT.resolve()
    if (
        not candidate.is_relative_to(root)
        or not candidate.is_file()
        or candidate.is_symlink()
    ):
        raise PublicError("Fichier temporaire introuvable.", status=404)
    name = (
        str(delivery.get("name") or candidate.name)
        .replace("\r", "")
        .replace("\n", "")
    )
    job.pop("download_ticket_hash", None)
    job.pop("download_ticket_expires", None)
    musicdl.atomic_write_json(path, job)

    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    response = web.StreamResponse(
        headers={
            "Content-Type": content_type,
            "Content-Length": str(candidate.stat().st_size),
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        }
    )
    await response.prepare(request)
    completed = False
    try:
        with candidate.open("rb") as handle:
            while chunk := await asyncio.to_thread(handle.read, 1024 * 1024):
                await response.write(chunk)
        await response.write_eof()
        completed = True
        return response
    finally:
        if completed:
            owner_id = str(uuid.UUID(str(job["owner_id"])))
            job_root = musicdl.DELIVERY_ROOT / owner_id / job_id
            if job_root.is_dir():
                shutil.rmtree(job_root)
            job["delivered_at"] = musicdl.utc_now()
            job["delivery_deleted"] = True
            job.pop("delivery", None)
            musicdl.atomic_write_json(path, job)


async def config_handler(_request: web.Request) -> web.Response:
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        raise SourceError("Supabase n'est pas configuré.")
    return web.json_response(
        {
            "supabase_url": SUPABASE_URL,
            "supabase_publishable_key": SUPABASE_PUBLISHABLE_KEY,
        }
    )


async def health_handler(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def index_handler(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(ASSET_ROOT / "index.html")


async def javascript_handler(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(ASSET_ROOT / "app.js", headers={"Content-Type": "text/javascript; charset=utf-8"})


async def stylesheet_handler(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(ASSET_ROOT / "style.css", headers={"Content-Type": "text/css; charset=utf-8"})


def create_app() -> web.Application:
    app = web.Application(middlewares=[api_middleware], client_max_size=JSON_BODY_LIMIT)
    app[SEARCH_SEMAPHORES_KEY] = {
        "tiddl": asyncio.Semaphore(1),
        "beatport": asyncio.Semaphore(1),
        "qobuz": asyncio.Semaphore(1),
    }
    app[STATUS_CACHE_KEY] = {
        "created_at": 0.0,
        "value": None,
        "lock": asyncio.Lock(),
    }
    app[AUTH_CACHE_KEY] = {}
    app.router.add_get("/", index_handler)
    app.router.add_get("/app.js", javascript_handler)
    app.router.add_get("/style.css", stylesheet_handler)
    app.router.add_get("/api/health", health_handler)
    app.router.add_get("/api/config", config_handler)
    app.router.add_get("/api/status", status_handler)
    app.router.add_post("/api/search", search_handler)
    app.router.add_post("/api/download", download_handler)
    app.router.add_post("/api/downloads", batch_download_handler)
    app.router.add_post("/api/download-ticket", ticket_handler)
    app.router.add_get("/api/files/{job_id}", file_handler)
    return app


def main() -> None:
    os.umask(0o077)
    musicdl.ensure_layout()
    for directory in (
        musicdl.STATE_ROOT / "tiddl",
        musicdl.STATE_ROOT / "beatportdl",
        musicdl.STATE_ROOT / "qobuz",
    ):
        directory.chmod(0o700)
    for path in (TIDAL_AUTH_PATH, BEATPORT_CACHE_PATH, musicdl.QOBUZ_CONFIG_PATH):
        if path.exists():
            path.chmod(0o600)

    host = os.environ.get("MUSICDL_WEB_HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("MUSICDL_WEB_PORT", "8080"))
    except ValueError as exc:
        raise SystemExit("MUSICDL_WEB_PORT doit être un nombre") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("MUSICDL_WEB_PORT doit être compris entre 1 et 65535")
    web.run_app(create_app(), host=host, port=port, access_log=None, print=None)


if __name__ == "__main__":
    main()
