#!/usr/bin/env python3
"""Durable queue and CLI wrapper for tiddl, BeatportDL and qobuz-dl."""

from __future__ import annotations

import argparse
import configparser
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse


PROVIDERS = ("tiddl", "beatport", "qobuz")
STATES = ("pending", "running", "done", "failed")

QUEUE_ROOT = Path(os.environ.get("MUSICDL_QUEUE_DIR", "/queue"))
STATE_ROOT = Path(os.environ.get("MUSICDL_STATE_DIR", "/state"))
CONFIG_ROOT = Path(os.environ.get("MUSICDL_CONFIG_DIR", "/config"))
SECRETS_ROOT = Path(os.environ.get("MUSICDL_SECRETS_DIR", "/run/secrets"))
TIDDL_DOWNLOAD_DIR = Path(
    os.environ.get("MUSICDL_TIDDL_DOWNLOAD_DIR", "/downloads/tiddl")
)
BEATPORT_DOWNLOAD_DIR = Path(
    os.environ.get("MUSICDL_BEATPORT_DOWNLOAD_DIR", "/downloads/beatport")
)
QOBUZ_DOWNLOAD_DIR = Path(
    os.environ.get("MUSICDL_QOBUZ_DOWNLOAD_DIR", "/downloads/qobuz")
)
DELIVERY_ROOT = Path(os.environ.get("MUSICDL_DELIVERY_DIR", "/deliveries"))
QOBUZ_CONFIG_PATH = STATE_ROOT / "qobuz" / ".config" / "qobuz-dl" / "config.ini"

TIDAL_RESOURCE_RE = re.compile(
    r"^(?:(?:track|video|album|artist)/[0-9]+|(?:playlist|mix)/[A-Za-z0-9-]+)$"
)
JOB_ID_RE = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}(?:-recovered-[0-9a-f]{6})?$"
)

STOP_EVENT = threading.Event()
ACTIVE_PROCESS: subprocess.Popen[bytes] | None = None
ACTIVE_JOB_PATH: Path | None = None
SIGNAL_COUNT = 0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_layout() -> None:
    for state in STATES:
        for provider in PROVIDERS:
            (QUEUE_ROOT / state / provider).mkdir(parents=True, exist_ok=True)
    for provider in PROVIDERS:
        (QUEUE_ROOT / "logs" / provider).mkdir(parents=True, exist_ok=True)
        (QUEUE_ROOT / "locks" / provider).mkdir(parents=True, exist_ok=True)

    (STATE_ROOT / "tiddl").mkdir(parents=True, exist_ok=True)
    (STATE_ROOT / "beatportdl").mkdir(parents=True, exist_ok=True)
    QOBUZ_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    (STATE_ROOT / "home").mkdir(parents=True, exist_ok=True)
    TIDDL_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    BEATPORT_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    QOBUZ_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DELIVERY_ROOT.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def validate_provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError(f"source inconnue: {provider}")
    return provider


def is_supported_beatport_path(path: str) -> bool:
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if len(segments) > 1 and len(segments[0]) == 2:
        segments = segments[1:]
        if segments and segments[0] == "catalog":
            segments = segments[1:]
    if not segments:
        return False

    if segments[0] in {"tracks", "releases"}:
        id_index = 1
    elif segments[0] == "library":
        if len(segments) < 2 or segments[1] not in {"playlist", "playlists"}:
            return False
        id_index = 2
    elif segments[0] in {
        "track",
        "release",
        "playlists",
        "chart",
        "playlist",
        "label",
        "artist",
    }:
        id_index = 2
    else:
        return False
    return len(segments) > id_index and segments[id_index].isdigit()


def is_supported_tidal_path(path: str) -> bool:
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if segments and segments[0] == "browse":
        segments = segments[1:]
    if len(segments) < 2:
        return False
    kind, resource_id = segments[0], segments[1]
    if kind in {"track", "video", "album", "artist"}:
        return resource_id.isdigit()
    return kind in {"playlist", "mix"} and re.fullmatch(
        r"[A-Za-z0-9-]+", resource_id
    ) is not None


def is_supported_qobuz_path(path: str) -> bool:
    segments = [segment for segment in path.strip("/").split("/") if segment]
    kinds = {"album", "artist", "track", "playlist", "label"}
    if len(segments) in {2, 3} and segments[0] in kinds:
        return all(segments[1:])
    if len(segments) in {3, 4}:
        return (
            re.fullmatch(r"[a-z]{2}-[a-z]{2}", segments[0]) is not None
            and segments[1] in kinds
            and all(segments[2:])
        )
    return False


def validate_url(provider: str, value: str) -> str:
    provider = validate_provider(provider)
    value = value.strip()
    if not value:
        raise ValueError("URL vide")

    if provider == "tiddl" and TIDAL_RESOURCE_RE.fullmatch(value):
        return value

    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not parsed.path:
        raise ValueError(f"URL invalide pour {provider}: {value}")

    if provider == "tiddl" and host in {
        "tidal.com",
        "www.tidal.com",
        "listen.tidal.com",
    } and is_supported_tidal_path(parsed.path):
        return value

    if provider == "beatport":
        if not is_supported_beatport_path(parsed.path):
            raise ValueError(f"URL invalide pour {provider}: {value}")
        if host == "beatport.com":
            return urlunparse(parsed._replace(netloc="www.beatport.com"))
        if host in {"www.beatport.com", "api.beatport.com"}:
            return value

    if provider == "qobuz" and host in {
        "www.qobuz.com",
        "open.qobuz.com",
        "play.qobuz.com",
    } and is_supported_qobuz_path(parsed.path):
        return value

    raise ValueError(f"URL invalide pour {provider}: {value}")


def normalized_urls(provider: str, values: Iterable[str]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        url = validate_url(provider, value)
        if url not in seen:
            urls.append(url)
            seen.add(url)
    if not urls:
        raise ValueError("aucune URL fournie")
    return urls


def enqueue(
    provider: str,
    values: Iterable[str],
    *,
    metadata: dict[str, str] | None = None,
    owner_id: str | None = None,
    ephemeral: bool = False,
) -> str:
    provider = validate_provider(provider)
    urls = normalized_urls(provider, values)
    job_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    payload: dict[str, Any] = {
        "schema": 1,
        "id": job_id,
        "provider": provider,
        "urls": urls,
        "created_at": utc_now(),
        "attempts": 0,
    }
    if owner_id is not None:
        try:
            payload["owner_id"] = str(uuid.UUID(owner_id))
        except ValueError as exc:
            raise ValueError("identifiant utilisateur invalide") from exc
    if ephemeral:
        if owner_id is None:
            raise ValueError("un téléchargement temporaire doit avoir un propriétaire")
        payload["ephemeral"] = True
    if metadata:
        display = {
            key: value.strip()
            for key, value in metadata.items()
            if key in {"title", "artist", "kind"}
            and isinstance(value, str)
            and value.strip()
        }
        if display:
            payload["display"] = display
    atomic_write_json(QUEUE_ROOT / "pending" / provider / f"{job_id}.json", payload)
    return job_id


def urls_from_file(path: Path) -> list[str]:
    values: list[str] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                values.append(stripped)
    return values


def load_job(path: Path, expected_provider: str | None = None) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("format de tâche invalide")

    provider = validate_provider(str(payload.get("provider", "")))
    if expected_provider is not None and provider != expected_provider:
        raise ValueError("source incohérente dans la tâche")
    payload["urls"] = normalized_urls(provider, payload.get("urls", []))
    if not isinstance(payload.get("id"), str) or not JOB_ID_RE.fullmatch(payload["id"]):
        raise ValueError("identifiant de tâche invalide")
    return payload


def sync_tiddl_config(download_dir: Path | None = None) -> None:
    if download_dir is None and os.environ.get("TUNESAVER_DESKTOP") == "1":
        download_dir = TIDDL_DOWNLOAD_DIR
    source = CONFIG_ROOT / "tiddl.toml"
    destination = STATE_ROOT / "tiddl" / "config.toml"
    if not source.is_file():
        raise RuntimeError(f"configuration tiddl absente: {source}")
    content = source.read_text(encoding="utf-8")
    if download_dir is not None:
        quoted = json.dumps(str(download_dir))
        content = re.sub(
            r"(?m)^download_path\s*=.*$", f"download_path = {quoted}", content
        )
        content = re.sub(
            r"(?m)^scan_path\s*=.*$", f"scan_path = {quoted}", content
        )
    content_bytes = content.encode("utf-8")
    if destination.is_file() and destination.read_bytes() == content_bytes:
        return

    fd, temporary_name = tempfile.mkstemp(
        prefix=".config.toml.", suffix=".tmp", dir=destination.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_secret(name: str) -> str:
    path = SECRETS_ROOT / name
    try:
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
    except FileNotFoundError as exc:
        raise RuntimeError(f"secret Docker absent: {name}") from exc
    if not value:
        raise RuntimeError(f"secret Docker vide: {name}")
    return value


def secure_provider_state(provider: str) -> None:
    provider = validate_provider(provider)
    state_directory = {
        "tiddl": STATE_ROOT / "tiddl",
        "beatport": STATE_ROOT / "beatportdl",
        "qobuz": STATE_ROOT / "qobuz",
    }[provider]
    directories = (state_directory,) if provider == "qobuz" else (
        state_directory,
        STATE_ROOT / "home",
    )
    for directory in directories:
        directory.chmod(0o700)

    sensitive_files = {
        "tiddl": (state_directory / "auth.json",),
        "beatport": (state_directory / "beatportdl-credentials.json",),
        "qobuz": (QOBUZ_CONFIG_PATH,),
    }[provider]
    for path in sensitive_files:
        if path.exists():
            path.chmod(0o600)


def acquire_provider_lock(
    provider: str, kind: str, *, blocking: bool = True
) -> Any:
    provider = validate_provider(provider)
    if kind not in {"enqueue", "operation", "worker"}:
        raise ValueError(f"type de verrou inconnu: {kind}")
    path = QUEUE_ROOT / "locks" / provider / f"{kind}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    path.chmod(0o600)
    flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        fcntl.flock(handle.fileno(), flags)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f"un worker {provider} est déjà actif") from exc
    return handle


def release_provider_lock(handle: Any) -> None:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@contextmanager
def provider_lock(provider: str, kind: str, *, blocking: bool = True) -> Any:
    handle = acquire_provider_lock(provider, kind, blocking=blocking)
    try:
        yield handle
    finally:
        release_provider_lock(handle)


def build_beatport_config(directory: Path, download_dir: Path | None = None) -> Path:
    source = CONFIG_ROOT / "beatport.json"
    try:
        settings = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"configuration BeatportDL absente: {source}") from exc
    if not isinstance(settings, dict):
        raise RuntimeError("config/beatport.json doit contenir un objet JSON")

    settings.update(
        {
            "username": read_secret("beatport_username"),
            "password": read_secret("beatport_password"),
            "downloads_directory": str(download_dir or BEATPORT_DOWNLOAD_DIR),
            "show_progress": False,
            "write_error_log": True,
        }
    )
    path = directory / "beatportdl-config.yml"
    # JSON is valid YAML and lets us escape credentials without an extra dependency.
    path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    # BeatportDL otherwise defaults its error log next to the read-only binary.
    error_log = directory / "beatportdl-err.log"
    error_log.touch(mode=0o600, exist_ok=True)
    return path


QOBUZ_DEFAULTS = {
    "default_folder": str(QOBUZ_DOWNLOAD_DIR),
    "default_quality": "27",
    "default_limit": "20",
    "no_m3u": "false",
    "albums_only": "false",
    "no_fallback": "false",
    "og_cover": "false",
    "embed_art": "false",
    "no_cover": "false",
    "no_database": "false",
    "folder_format": "{artist} - {album} ({year}) [{bit_depth}B-{sampling_rate}kHz]",
    "track_format": "{tracknumber}. {tracktitle}",
    "smart_discography": "false",
}
QOBUZ_AUTH_KEYS = {
    "user_id",
    "user_auth_token",
    "app_id",
    "secrets",
    "musicdl_login_id",
}


def qobuz_login_id(user_id: str, token: str) -> str:
    return hashlib.sha256(f"{user_id}\0{token}".encode("utf-8")).hexdigest()


def load_qobuz_settings() -> dict[str, str]:
    settings = dict(QOBUZ_DEFAULTS)
    source = CONFIG_ROOT / "qobuz.ini"
    parser = configparser.ConfigParser()
    try:
        with source.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except FileNotFoundError as exc:
        raise RuntimeError(f"configuration qobuz-dl absente: {source}") from exc
    except configparser.Error as exc:
        raise RuntimeError(f"configuration qobuz-dl invalide: {exc}") from exc
    if not parser.has_section("download"):
        raise RuntimeError("config/qobuz.ini doit contenir une section [download]")
    unknown = set(parser["download"]) - set(QOBUZ_DEFAULTS)
    if unknown:
        raise RuntimeError(f"option Qobuz inconnue: {sorted(unknown)[0]}")
    settings.update(parser["download"])
    if settings["default_quality"] not in {"5", "6", "7", "27"}:
        raise RuntimeError("la qualité Qobuz doit être 5, 6, 7 ou 27")
    if not settings["default_limit"].isdigit() or int(settings["default_limit"]) < 1:
        raise RuntimeError("default_limit Qobuz doit être un entier positif")
    boolean_keys = {
        "no_m3u",
        "albums_only",
        "no_fallback",
        "og_cover",
        "embed_art",
        "no_cover",
        "no_database",
        "smart_discography",
    }
    for key in boolean_keys:
        if settings[key].lower() not in {"true", "false", "yes", "no", "1", "0"}:
            raise RuntimeError(f"{key} Qobuz doit être un booléen")
    settings["default_folder"] = str(QOBUZ_DOWNLOAD_DIR)
    return settings


def read_qobuz_config() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    try:
        with QOBUZ_CONFIG_PATH.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except FileNotFoundError as exc:
        raise RuntimeError("Qobuz non authentifié: lancez ./musicdl auth qobuz") from exc
    except configparser.Error as exc:
        raise RuntimeError("authentification Qobuz illisible") from exc
    return parser


def write_qobuz_config(values: dict[str, str]) -> None:
    QOBUZ_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser()
    parser["DEFAULT"] = values
    fd, temporary_name = tempfile.mkstemp(
        prefix=".config.ini.", suffix=".tmp", dir=QOBUZ_CONFIG_PATH.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            parser.write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, QOBUZ_CONFIG_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)


def sync_qobuz_config() -> None:
    settings = load_qobuz_settings()
    current = dict(read_qobuz_config().defaults())
    if QOBUZ_AUTH_KEYS - set(current):
        raise RuntimeError(
            "authentification Qobuz incomplète; relancez ./musicdl auth qobuz --force"
        )
    write_qobuz_config(
        {**settings, **{key: current[key] for key in QOBUZ_AUTH_KEYS}}
    )


def auth_qobuz(*, force: bool) -> None:
    secure_provider_state("qobuz")
    user_id = read_secret("qobuz_user_id")
    token = read_secret("qobuz_token")
    login_id = qobuz_login_id(user_id, token)
    if QOBUZ_CONFIG_PATH.is_file() and not force:
        if read_qobuz_config().defaults().get("musicdl_login_id") == login_id:
            print("Qobuz est déjà authentifié pour ce compte.")
            return
    try:
        from qobuz_dl.bundle import Bundle
        from qobuz_dl.qopy import Client

        bundle = Bundle()
        app_id = str(bundle.get_app_id())
        secrets = list(bundle.get_secrets().values())
        Client.from_token(user_id, token, app_id, secrets)
    except Exception as exc:
        raise RuntimeError(
            "connexion Qobuz par jeton impossible "
            f"({type(exc).__name__}); récupérez un nouveau jeton depuis play.qobuz.com"
        ) from exc
    values = load_qobuz_settings()
    values.update(
        {
            "user_id": user_id,
            "user_auth_token": token,
            "app_id": app_id,
            "secrets": ",".join(secrets),
            "musicdl_login_id": login_id,
        }
    )
    write_qobuz_config(values)
    print("Compte Qobuz authentifié.")


def _write_output(chunk: bytes, log_handle: Any) -> None:
    log_handle.write(chunk)
    log_handle.flush()
    try:
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    except AttributeError:
        sys.stdout.write(chunk.decode("utf-8", errors="replace"))
        sys.stdout.flush()


def stream_command(
    command: list[str], *, cwd: Path | None, log_path: Path, environment: dict[str, str]
) -> int:
    global ACTIVE_PROCESS

    if ACTIVE_JOB_PATH is not None and not ACTIVE_JOB_PATH.exists():
        return 1

    log_path.parent.mkdir(parents=True, exist_ok=True)
    printable_command = shlex.join(command)
    print(f"[musicdl] commande: {printable_command}", flush=True)
    with log_path.open("ab") as log_handle:
        log_handle.write(f"\n[{utc_now()}] {printable_command}\n".encode())
        log_handle.flush()
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        ACTIVE_PROCESS = process
        try:
            assert process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    if (ACTIVE_JOB_PATH is not None and not ACTIVE_JOB_PATH.exists()) or not desktop_parent_alive():
                        if not desktop_parent_alive():
                            STOP_EVENT.set()
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        break
                    if not selector.select(timeout=0.25):
                        continue
                    chunk = process.stdout.read1(65536)
                    if not chunk:
                        break
                    _write_output(chunk, log_handle)
            return process.wait()
        finally:
            ACTIVE_PROCESS = None
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            if process.stdout is not None:
                process.stdout.close()


def runtime_environment(provider: str | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    home = STATE_ROOT / "qobuz" if provider == "qobuz" else STATE_ROOT / "home"
    environment.update(
        {
            "TIDDL_PATH": str(STATE_ROOT / "tiddl"),
            "XDG_CONFIG_HOME": str(STATE_ROOT),
            "XDG_STATE_HOME": str(STATE_ROOT),
            "HOME": str(home),
        }
    )
    environment.pop("TIDDL_AUTH", None)
    return environment


def _run_provider_unlocked(
    provider: str,
    values: Iterable[str],
    log_path: Path,
    *,
    output_dir: Path | None = None,
) -> int:
    provider = validate_provider(provider)
    urls = normalized_urls(provider, values)
    environment = runtime_environment(provider)

    if provider == "tiddl":
        sync_tiddl_config(output_dir)
        command = ([sys.executable, str(Path(__file__).with_name("desktop_tiddl.py"))]
                   if os.environ.get("TUNESAVER_DESKTOP") == "1" else ["tiddl"])
        command.extend(["download", "--raise-errors", "url", *urls])
        try:
            return stream_command(
                command, cwd=None, log_path=log_path, environment=environment
            )
        finally:
            if output_dir is not None:
                sync_tiddl_config()

    if provider == "qobuz":
        sync_qobuz_config()
        quality = load_qobuz_settings()["default_quality"]
        command = [
            sys.executable,
            "-m",
            "qobuz_dl.cli",
            "dl",
            *urls,
            "-d",
            str(output_dir or QOBUZ_DOWNLOAD_DIR),
            "-q",
            quality,
        ]
        # Each web job owns a separate delivery directory. A global ID registry
        # cannot prove that this user's file still exists (especially after a reset).
        if output_dir is not None:
            command.append("--no-db")
        return stream_command(command, cwd=None, log_path=log_path, environment=environment)

    with tempfile.TemporaryDirectory(prefix="musicdl-beatport-") as temporary_name:
        temporary_dir = Path(temporary_name)
        build_beatport_config(temporary_dir, output_dir)
        command = ["beatportdl", "-q", *urls]
        return_code = stream_command(
            command,
            cwd=temporary_dir,
            log_path=log_path,
            environment=environment,
        )
        error_log = temporary_dir / "beatportdl-err.log"
        if error_log.is_file() and error_log.stat().st_size:
            marker = (
                b"\n[musicdl] BeatportDL a produit un journal d'erreurs; "
                b"la tache est placee dans failed.\n"
            )
            with log_path.open("ab") as log_handle:
                _write_output(marker, log_handle)
                _write_output(error_log.read_bytes(), log_handle)
            return return_code or 1
        return return_code


def run_provider(
    provider: str,
    values: Iterable[str],
    log_path: Path,
    *,
    output_dir: Path | None = None,
) -> int:
    provider = validate_provider(provider)
    secure_provider_state(provider)
    with provider_lock(provider, "operation"):
        return _run_provider_unlocked(
            provider, values, log_path, output_dir=output_dir
        )


AUDIO_EXTENSIONS = {
    ".aac", ".aiff", ".alac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"
}
DEFAULT_VALUE_CENTS = {"tiddl": 129, "beatport": 149, "qobuz": 149}


def estimated_track_value_cents(provider: str) -> int:
    provider = validate_provider(provider)
    variable = f"MUSICDL_{provider.upper()}_TRACK_VALUE_CENTS"
    try:
        value = int(os.environ.get(variable, DEFAULT_VALUE_CENTS[provider]))
    except ValueError:
        value = DEFAULT_VALUE_CENTS[provider]
    return min(max(value, 0), 10000)


def prepare_delivery(payload: dict[str, Any], content_dir: Path) -> None:
    files = sorted(
        path for path in content_dir.rglob("*") if path.is_file() and not path.is_symlink()
    )
    if not files:
        raise RuntimeError("aucun fichier téléchargeable n'a été produit")

    audio_files = [path for path in files if path.suffix.lower() in AUDIO_EXTENSIONS]
    audio_count = len(audio_files)
    if audio_count == 1:
        delivery_path = audio_files[0]
    elif len(files) == 1:
        delivery_path = files[0]
    else:
        delivery_path = content_dir.parent / f"TuneSaver-{payload['id']}.zip"
        with zipfile.ZipFile(
            delivery_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for path in files:
                archive.write(path, path.relative_to(content_dir))
        os.chmod(delivery_path, 0o600)

    relative_path = delivery_path.relative_to(DELIVERY_ROOT)
    track_count = max(audio_count, len(payload.get("urls", [])))
    payload["track_count"] = track_count
    payload["estimated_savings_cents"] = (
        track_count * estimated_track_value_cents(str(payload["provider"]))
    )
    payload["delivery"] = {
        "name": delivery_path.name,
        "path": str(relative_path),
        "size": delivery_path.stat().st_size,
    }


def recover_running_jobs(provider: str) -> int:
    with provider_lock(provider, "enqueue"):
        return _recover_running_jobs(provider)


def _recover_running_jobs(provider: str) -> int:
    recovered = 0
    running_dir = QUEUE_ROOT / "running" / provider
    pending_dir = QUEUE_ROOT / "pending" / provider
    for source in sorted(running_dir.glob("*.json")):
        destination = pending_dir / source.name
        if destination.exists():
            suffix = f"recovered-{uuid.uuid4().hex[:6]}"
            try:
                payload = load_job(source, expected_provider=provider)
                payload["id"] = f"{payload['id']}-{suffix}"
                atomic_write_json(source, payload)
                destination = pending_dir / f"{payload['id']}.json"
            except Exception:
                destination = pending_dir / f"{source.stem}-{suffix}.json"
        os.replace(source, destination)
        recovered += 1
    return recovered


def remove_job_files(provider: str, owner_id: str, job_id: str) -> None:
    validate_provider(provider)
    owner_id = str(uuid.UUID(owner_id))
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("identifiant de tâche invalide")
    root = DELIVERY_ROOT.resolve()
    owner_root = DELIVERY_ROOT / owner_id
    job_root = owner_root / job_id
    if owner_root.is_symlink() or not owner_root.resolve().is_relative_to(root):
        raise ValueError("dossier utilisateur invalide")
    if job_root.is_symlink():
        job_root.unlink()
    elif job_root.exists():
        shutil.rmtree(job_root)
    for log in (QUEUE_ROOT / "logs" / provider).glob(f"{job_id}-attempt-*.log"):
        log.unlink(missing_ok=True)


def process_job(provider: str, pending_path: Path) -> bool:
    global ACTIVE_JOB_PATH
    running_path = QUEUE_ROOT / "running" / provider / pending_path.name
    with provider_lock(provider, "enqueue"):
        try:
            os.replace(pending_path, running_path)
        except FileNotFoundError:
            return False
        try:
            payload = load_job(running_path, expected_provider=provider)
        except Exception as exc:
            failed_path = QUEUE_ROOT / "failed" / provider / running_path.name
            print(f"[musicdl] tâche illisible {running_path.name}: {exc}", file=sys.stderr)
            os.replace(running_path, failed_path)
            return True
        payload["attempts"] = int(payload.get("attempts", 0)) + 1
        payload["last_started_at"] = utc_now()
        payload.pop("last_error", None)
        atomic_write_json(running_path, payload)

    attempt = payload["attempts"]
    log_path = QUEUE_ROOT / "logs" / provider / f"{payload['id']}-attempt-{attempt}.log"
    print(
        f"[musicdl] démarrage {payload['id']} ({provider}, {len(payload['urls'])} URL)",
        flush=True,
    )
    output_dir: Path | None = None
    if payload.get("ephemeral") is True:
        try:
            owner_id = str(uuid.UUID(str(payload.get("owner_id", ""))))
        except ValueError as exc:
            payload["last_error"] = f"ValueError: propriétaire invalide ({exc})"
        else:
            job_root = DELIVERY_ROOT / owner_id / str(payload["id"])
            if job_root.exists():
                shutil.rmtree(job_root)
            output_dir = job_root / "content"
            output_dir.mkdir(parents=True, mode=0o700)
    try:
        if payload.get("ephemeral") is True and output_dir is None:
            raise RuntimeError("dossier temporaire utilisateur invalide")
        ACTIVE_JOB_PATH = running_path
        return_code = run_provider(
            provider, payload["urls"], log_path, output_dir=output_dir
        )
        if return_code == 0 and output_dir is not None:
            prepare_delivery(payload, output_dir)
        error: str | None = None
    except Exception as exc:
        return_code = 1
        error = f"{type(exc).__name__}: {exc}"
        print(f"[musicdl] {error}", file=sys.stderr, flush=True)
        with log_path.open("a", encoding="utf-8") as log_handle:
            log_handle.write(f"\n[musicdl] {error}\n")
    finally:
        ACTIVE_JOB_PATH = None

    payload["last_finished_at"] = utc_now()
    payload["last_exit_code"] = return_code
    payload["last_log"] = str(log_path.relative_to(QUEUE_ROOT))
    if error:
        payload["last_error"] = error
    state = "done" if return_code == 0 else "failed"
    destination = QUEUE_ROOT / state / provider / running_path.name
    with provider_lock(provider, "enqueue"):
        # A deleted running file cancels the job, including any partial output.
        if not running_path.exists():
            if payload.get("ephemeral") is True:
                remove_job_files(provider, payload["owner_id"], payload["id"])
            return True
        atomic_write_json(running_path, payload)
        os.replace(running_path, destination)
    print(f"[musicdl] {payload['id']} -> {state} (code {return_code})", flush=True)
    return True


def signal_handler(signum: int, _frame: Any) -> None:
    global SIGNAL_COUNT
    SIGNAL_COUNT += 1
    STOP_EVENT.set()
    if os.environ.get("TUNESAVER_DESKTOP") == "1":
        if ACTIVE_PROCESS is not None and ACTIVE_PROCESS.poll() is None:
            try:
                os.killpg(ACTIVE_PROCESS.pid, signal.SIGTERM if SIGNAL_COUNT == 1 else signal.SIGKILL)
            except ProcessLookupError:
                pass
        return
    if SIGNAL_COUNT == 1:
        print(
            f"[musicdl] signal {signum}: arrêt après la tâche en cours",
            file=sys.stderr,
            flush=True,
        )
        return

    if ACTIVE_PROCESS is not None and ACTIVE_PROCESS.poll() is None:
        print("[musicdl] second signal: interruption forcée", file=sys.stderr, flush=True)
        ACTIVE_PROCESS.terminate()


def desktop_parent_alive() -> bool:
    parent = os.environ.get("TUNESAVER_PARENT_PID")
    return not parent or str(os.getppid()) == parent


def worker(provider: str, *, once: bool, poll_interval: float) -> int:
    provider = validate_provider(provider)
    if not math.isfinite(poll_interval) or poll_interval <= 0:
        raise ValueError("l'intervalle de polling doit être un nombre positif et fini")
    ensure_layout()
    secure_provider_state(provider)
    if provider == "tiddl":
        sync_tiddl_config()
    elif provider == "qobuz":
        sync_qobuz_config()

    with provider_lock(provider, "worker", blocking=False):
        recovered = recover_running_jobs(provider)
        if recovered:
            print(f"[musicdl] {recovered} tâche(s) récupérée(s) après redémarrage")

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        print(f"[musicdl] worker {provider} prêt", flush=True)

        while not STOP_EVENT.is_set() and desktop_parent_alive():
            pending = sorted((QUEUE_ROOT / "pending" / provider).glob("*.json"))
            if pending:
                process_job(provider, pending[0])
                continue
            if once:
                break
            STOP_EVENT.wait(poll_interval)
        return 0


def retry_failed(provider: str, job_id: str | None) -> int:
    providers = PROVIDERS if provider == "all" else (validate_provider(provider),)
    moved = 0
    for item_provider in providers:
        failed_dir = QUEUE_ROOT / "failed" / item_provider
        candidates = sorted(failed_dir.glob("*.json"))
        if job_id:
            candidates = [path for path in candidates if path.stem == job_id]
        for source in candidates:
            destination = QUEUE_ROOT / "pending" / item_provider / source.name
            if destination.exists():
                continue
            try:
                payload = load_job(source, expected_provider=item_provider)
            except Exception as exc:
                print(f"tâche ignorée ({source.name}): {exc}", file=sys.stderr)
                continue
            payload["retried_at"] = utc_now()
            atomic_write_json(source, payload)
            os.replace(source, destination)
            moved += 1
    print(f"{moved} tâche(s) replacée(s) dans pending")
    return 0 if moved or not job_id else 1


def show_status(provider: str, limit: int) -> int:
    providers = PROVIDERS if provider == "all" else (validate_provider(provider),)
    print("STATE      TIDDL  BEATPORT  QOBUZ")
    for state in STATES:
        counts = []
        for item_provider in PROVIDERS:
            if item_provider in providers:
                counts.append(len(list((QUEUE_ROOT / state / item_provider).glob("*.json"))))
            else:
                counts.append(0)
        print(f"{state:<10} {counts[0]:>5}  {counts[1]:>8}  {counts[2]:>5}")

    recent: list[tuple[float, str, str, Path]] = []
    for state in STATES:
        for item_provider in providers:
            for path in (QUEUE_ROOT / state / item_provider).glob("*.json"):
                recent.append((path.stat().st_mtime, state, item_provider, path))
    if recent:
        print("\nTâches récentes:")
        for _, state, item_provider, path in sorted(recent, reverse=True)[:limit]:
            try:
                payload = load_job(path, expected_provider=item_provider)
                attempts = payload.get("attempts", 0)
                print(f"  {payload['id']}  {item_provider:<8} {state:<7} essais={attempts}")
            except Exception:
                print(f"  {path.stem}  {item_provider:<8} {state:<7} illisible")
    return 0


def doctor(provider: str) -> int:
    failures: list[str] = []
    if provider in {"all", "tiddl"}:
        try:
            config_data = tomllib.loads(
                (CONFIG_ROOT / "tiddl.toml").read_text(encoding="utf-8")
            )
            if not isinstance(config_data.get("download"), dict):
                raise ValueError("section [download] manquante")
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            failures.append(f"configuration tiddl invalide: {exc}")
        auth_path = STATE_ROOT / "tiddl" / "auth.json"
        if not auth_path.is_file():
            failures.append("TIDAL non authentifié: lancez ./musicdl auth tiddl")
        else:
            try:
                auth_data = json.loads(auth_path.read_text(encoding="utf-8"))
                required = ("token", "refresh_token", "user_id", "country_code")
                if not isinstance(auth_data, dict) or any(
                    not isinstance(auth_data.get(key), str) or not auth_data[key]
                    for key in required
                ):
                    raise ValueError("champs d'authentification manquants")
                auth_path.chmod(0o600)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                failures.append(
                    f"authentification TIDAL illisible ({exc}); "
                    "lancez ./musicdl auth tiddl --force"
                )
    if provider in {"all", "beatport"}:
        try:
            beatport_settings = json.loads(
                (CONFIG_ROOT / "beatport.json").read_text(encoding="utf-8")
            )
            if not isinstance(beatport_settings, dict):
                raise ValueError("un objet JSON est attendu")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"configuration BeatportDL invalide: {exc}")
        for name in ("beatport_username", "beatport_password"):
            try:
                read_secret(name)
            except RuntimeError as exc:
                failures.append(str(exc))
    if provider in {"all", "qobuz"}:
        try:
            load_qobuz_settings()
        except RuntimeError as exc:
            failures.append(str(exc))
        try:
            user_id = read_secret("qobuz_user_id")
            token = read_secret("qobuz_token")
            defaults = read_qobuz_config().defaults()
            if QOBUZ_AUTH_KEYS - set(defaults):
                raise RuntimeError("authentification Qobuz incomplète")
            if defaults.get("musicdl_login_id") != qobuz_login_id(user_id, token):
                raise RuntimeError(
                    "le compte Qobuz a changé; lancez ./musicdl auth qobuz --force"
                )
            QOBUZ_CONFIG_PATH.chmod(0o600)
        except RuntimeError as exc:
            failures.append(str(exc))
    if failures:
        for failure in failures:
            print(f"ERREUR: {failure}", file=sys.stderr)
        return 1
    print("Fichiers d'authentification présents et lisibles")
    return 0


def exec_tiddl_auth(*, force: bool) -> None:
    secure_provider_state("tiddl")
    lock_handle = acquire_provider_lock("tiddl", "operation")
    environment = runtime_environment()
    try:
        if force:
            atomic_write_json(STATE_ROOT / "tiddl" / "auth.json", {})
        os.set_inheritable(lock_handle.fileno(), True)
        os.execvpe(
            "tiddl",
            ["tiddl", "auth", "login", "--no-browser"],
            environment,
        )
    except Exception:
        release_provider_lock(lock_handle)
        raise


def positive_finite_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("nombre attendu") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("un nombre positif et fini est requis")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="musicdl")
    commands = parser.add_subparsers(dest="command", required=True)

    worker_parser = commands.add_parser("worker")
    worker_parser.add_argument("provider", choices=PROVIDERS)
    worker_parser.add_argument("--once", action="store_true")
    worker_parser.add_argument(
        "--poll-interval",
        type=positive_finite_float,
        default=os.environ.get("MUSICDL_POLL_INTERVAL", "5"),
    )

    enqueue_parser = commands.add_parser("enqueue")
    enqueue_parser.add_argument("provider", choices=PROVIDERS)
    enqueue_parser.add_argument("urls", nargs="+")

    enqueue_file_parser = commands.add_parser("enqueue-file")
    enqueue_file_parser.add_argument("provider", choices=PROVIDERS)
    enqueue_file_parser.add_argument("path", type=Path)

    run_parser = commands.add_parser("run")
    run_parser.add_argument("provider", choices=PROVIDERS)
    run_parser.add_argument("urls", nargs="+")

    status_parser = commands.add_parser("status")
    status_parser.add_argument("provider", choices=("all", *PROVIDERS), default="all", nargs="?")
    status_parser.add_argument("--limit", type=int, default=20)

    retry_parser = commands.add_parser("retry")
    retry_parser.add_argument("provider", choices=("all", *PROVIDERS), default="all", nargs="?")
    retry_parser.add_argument("job_id", nargs="?")

    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("provider", choices=("all", *PROVIDERS), default="all", nargs="?")

    auth_parser = commands.add_parser("auth-tiddl")
    auth_parser.add_argument(
        "--force",
        action="store_true",
        help="réinitialise l'authentification TIDAL locale avant la connexion",
    )
    qobuz_auth_parser = commands.add_parser("auth-qobuz")
    qobuz_auth_parser.add_argument(
        "--force",
        action="store_true",
        help="recrée la session qobuz-dl avec les secrets locaux",
    )
    commands.add_parser("versions")
    return parser


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = build_parser().parse_args(argv)
    try:
        ensure_layout()
        if args.command == "worker":
            return worker(args.provider, once=args.once, poll_interval=args.poll_interval)
        if args.command == "enqueue":
            job_id = enqueue(args.provider, args.urls)
            print(f"Tâche ajoutée: {job_id}")
            return 0
        if args.command == "enqueue-file":
            job_id = enqueue(args.provider, urls_from_file(args.path))
            print(f"Tâche ajoutée: {job_id}")
            return 0
        if args.command == "run":
            log_path = QUEUE_ROOT / "logs" / args.provider / f"manual-{int(time.time())}.log"
            return run_provider(args.provider, args.urls, log_path)
        if args.command == "status":
            return show_status(args.provider, max(args.limit, 1))
        if args.command == "retry":
            return retry_failed(args.provider, args.job_id)
        if args.command == "doctor":
            if args.provider in {"all", "tiddl"}:
                secure_provider_state("tiddl")
            if args.provider in {"all", "beatport"}:
                secure_provider_state("beatport")
            if args.provider in {"all", "qobuz"}:
                secure_provider_state("qobuz")
            return doctor(args.provider)
        if args.command == "auth-tiddl":
            sync_tiddl_config()
            exec_tiddl_auth(force=args.force)
        if args.command == "auth-qobuz":
            with provider_lock("qobuz", "operation"):
                auth_qobuz(force=args.force)
            return 0
        if args.command == "versions":
            print(f"tiddl={os.environ.get('TIDDL_VERSION', 'inconnue')}")
            print(f"beatportdl={os.environ.get('BEATPORTDL_COMMIT', 'inconnu')}")
            print(f"qobuz-dl={os.environ.get('QOBUZDL_COMMIT', 'inconnu')}")
            return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"musicdl: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
