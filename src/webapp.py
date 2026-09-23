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
import smtplib
import sqlite3
import subprocess
import tempfile
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse

import requests
from aiohttp import web
from mutagen import File as MutagenFile

import musicdl
import mixer_analysis
import playlist_sources


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
LOCAL_DB_PATH = Path(
    os.environ.get("TUNESAVER_DB_PATH", str(musicdl.STATE_ROOT / "tiddl" / "tunesaver.db"))
)

TIDAL_SEARCH_URL = "https://api.tidal.com/v1/search"
BEATPORT_SEARCH_URL = "https://api.beatport.com/v4/catalog/search/"
RESULT_LIMIT = 8
SEARCH_TEXT_LIMIT = 200
DIRECT_LINK_LIMIT = 2048
JSON_BODY_LIMIT = 131072
STATUS_CACHE_SECONDS = 3
PROVIDER_STATUS_SECONDS = 60
AUTH_CACHE_SECONDS = 60
DOWNLOAD_TICKET_SECONDS = 120
STREAM_TICKET_SECONDS = 12 * 60 * 60
MAX_BATCH_LINKS = 50
PASSWORD_ITERATIONS = 260_000
ACCESS_TOKEN_SECONDS = 12 * 60 * 60
REFRESH_TOKEN_SECONDS = 30 * 24 * 60 * 60
EMAIL_CODE_SECONDS = 20 * 60
ALLOWED_HOSTS = {
    host.strip().lower()
    for host in os.environ.get(
        "TUNESAVER_ALLOWED_HOSTS", "127.0.0.1,localhost,::1"
    ).split(",")
    if host.strip()
}
PUBLIC_URL = os.environ.get("TUNESAVER_PUBLIC_URL", "").rstrip("/")
ADMIN_EMAIL = os.environ.get("TUNESAVER_ADMIN_EMAIL", "admin")
ADMIN_PASSWORD = os.environ.get("TUNESAVER_ADMIN_PASSWORD", "")
ADMIN_SESSION_SECONDS = 12 * 60 * 60
SMTP_HOST = os.environ.get("TUNESAVER_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("TUNESAVER_SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("TUNESAVER_SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("TUNESAVER_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("TUNESAVER_SMTP_FROM", "tunesaver@galagain.com")
SMTP_STARTTLS = os.environ.get("TUNESAVER_SMTP_STARTTLS", "1") != "0"

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
ADMIN_SESSIONS_KEY = web.AppKey("admin_sessions", dict)
PLAYLIST_TICKETS_KEY = web.AppKey("playlist_tickets", dict)
PLAYLIST_ZIP_LOCK_KEY = web.AppKey("playlist_zip_lock", asyncio.Lock)


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
    style_nonce = request.get("style_nonce")
    style_sources = "'self'" + (f" 'nonce-{style_nonce}'" if style_nonce else "")
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; base-uri 'none'; connect-src {' '.join(connect_sources)}; "
        "font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
        "img-src 'self' blob: https:; media-src 'self' blob:; object-src 'none'; "
        f"script-src 'self'; style-src {style_sources}"
    )
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), geolocation=(), microphone=(), payment=(), usb=(), midi=(), speaker-selection=()"
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


def utc_timestamp() -> str:
    return musicdl.utc_now()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@contextmanager
def local_db() -> Any:
    LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(LOCAL_DB_PATH, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_local_store() -> None:
    with local_db() as db:
        db.executescript(
            """
            create table if not exists users (
              id text primary key,
              email text not null unique,
              password_salt text not null,
              password_hash text not null,
              email_verified integer not null default 0,
              approved integer not null default 0,
              blocked integer not null default 0,
              created_at text not null,
              last_sign_in_at text
            );

            create table if not exists sessions (
              token_hash text primary key,
              user_id text not null references users(id) on delete cascade,
              kind text not null check (kind in ('access', 'refresh')),
              expires_at real not null,
              created_at text not null
            );

            create index if not exists sessions_user_kind_idx
              on sessions (user_id, kind);

            create table if not exists stream_tickets (
              token_hash text primary key,
              user_id text not null references users(id) on delete cascade,
              job_id text not null,
              expires_at real not null
            );
            create index if not exists stream_tickets_expiry_idx on stream_tickets (expires_at);
            create index if not exists stream_tickets_job_idx on stream_tickets (job_id, user_id);

            create table if not exists email_codes (
              code_hash text primary key,
              user_id text not null references users(id) on delete cascade,
              purpose text not null check (purpose in ('verify_email', 'password_reset')),
              expires_at real not null,
              used_at text,
              created_at text not null
            );

            create index if not exists email_codes_user_purpose_idx
              on email_codes (user_id, purpose, expires_at desc);

            create table if not exists download_history (
              job_id text primary key,
              user_id text not null references users(id) on delete cascade,
              provider text not null,
              source_url text not null default '',
              title text not null default '',
              artist text not null default '',
              resource_kind text not null default 'track',
              state text not null,
              track_count integer not null default 0,
              estimated_savings_cents integer not null default 0,
              created_at text not null,
              completed_at text,
              delivered_at text
            );

            create index if not exists download_history_user_created_idx
              on download_history (user_id, created_at desc);

            create index if not exists download_history_state_created_idx
              on download_history (state, created_at desc);

            create table if not exists library_removed (
              user_id text not null references users(id) on delete cascade,
              job_id text not null,
              primary key (user_id, job_id)
            );

            create table if not exists library_orders (
              user_id text not null references users(id) on delete cascade,
              scope text not null,
              track_ids text not null,
              primary key (user_id, scope)
            );

            create table if not exists playlists (
              id text primary key,
              user_id text not null references users(id) on delete cascade,
              name text not null,
              created_at text not null
            );

            create index if not exists playlists_user_created_idx
              on playlists (user_id, created_at desc);

            create table if not exists playlist_folders (
              id text primary key,
              user_id text not null references users(id) on delete cascade,
              name text not null,
              collapsed integer not null default 0
            );
            create table if not exists playlist_folder_items (
              playlist_id text primary key references playlists(id) on delete cascade,
              folder_id text not null references playlist_folders(id) on delete cascade
            );

            create table if not exists playlist_items (
              playlist_id text not null references playlists(id) on delete cascade,
              job_id text not null,
              created_at text not null,
              primary key (playlist_id, job_id)
            );

            create table if not exists playlist_imports (
              id text primary key,
              user_id text not null references users(id) on delete cascade,
              playlist_id text not null references playlists(id) on delete cascade,
              source_url text not null,
              state text not null default 'pending',
              tracks text not null default '[]',
              processed integer not null default 0,
              queued integer not null default 0,
              failures text not null default '[]',
              error text not null default '',
              created_at text not null
            );

            create table if not exists tags (
              id text primary key,
              user_id text not null references users(id) on delete cascade,
              name text not null,
              color text not null,
              created_at text not null
            );

            create index if not exists tags_user_created_idx
              on tags (user_id, created_at desc);

            create table if not exists tag_items (
              tag_id text not null references tags(id) on delete cascade,
              job_id text not null,
              created_at text not null,
              primary key (tag_id, job_id)
            );
            """
        )
        columns = {
            row["name"]
            for row in db.execute("pragma table_info(users)").fetchall()
        }
        if "email_verified" not in columns:
            db.execute(
                "alter table users add column email_verified integer not null default 0"
            )
    mixer_analysis.init_store(local_db)


def hash_password(password: str, *, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        bytes.fromhex(salt),
        PASSWORD_ITERATIONS,
    )
    return salt, digest.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    try:
        _salt, digest = hash_password(password, salt=salt)
    except ValueError:
        return False
    return secrets.compare_digest(digest, expected_hash)


def require_mail_config() -> None:
    if not SMTP_HOST or not SMTP_FROM:
        raise SourceError("Envoi d'e-mail non configuré.")


def send_email(to_email: str, subject: str, body: str) -> None:
    require_mail_config()
    message = EmailMessage()
    message["From"] = SMTP_FROM
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            if SMTP_STARTTLS:
                smtp.starttls()
            if SMTP_USERNAME or SMTP_PASSWORD:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise SourceError("Impossible d'envoyer l'e-mail pour le moment.") from exc


def code_hash(email: str, purpose: str, code: str) -> str:
    return hashlib.sha256(f"{email.lower()}:{purpose}:{code}".encode()).hexdigest()


def create_email_code(user_id: str, email: str, purpose: str) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = time.time()
    with local_db() as db:
        db.execute(
            """
            update email_codes
            set used_at = ?
            where user_id = ? and purpose = ? and used_at is null
            """,
            (utc_timestamp(), user_id, purpose),
        )
        db.execute(
            """
            insert into email_codes (code_hash, user_id, purpose, expires_at, created_at)
            values (?, ?, ?, ?, ?)
            """,
            (
                code_hash(email, purpose, code),
                user_id,
                purpose,
                now + EMAIL_CODE_SECONDS,
                utc_timestamp(),
            ),
        )
    return code


def send_verification_code(user_id: str, email: str) -> None:
    code = create_email_code(user_id, email, "verify_email")
    send_email(
        email,
        "Code de verification TuneSaver",
        (
            "Voici ton code TuneSaver :\n\n"
            f"{code}\n\n"
            "Il expire dans 20 minutes. Si tu n'as pas demandé ce code, ignore ce message."
        ),
    )


def send_password_reset_code(user_id: str, email: str) -> None:
    code = create_email_code(user_id, email, "password_reset")
    send_email(
        email,
        "Code de reinitialisation TuneSaver",
        (
            "Voici ton code pour changer ton mot de passe TuneSaver :\n\n"
            f"{code}\n\n"
            "Il expire dans 20 minutes. Si tu n'as rien demandé, ignore ce message."
        ),
    )


def consume_email_code(email: str, purpose: str, code: str) -> dict[str, Any] | None:
    now = time.time()
    with local_db() as db:
        row = db.execute(
            """
            select users.*
            from email_codes
            join users on users.id = email_codes.user_id
            where users.email = ?
              and email_codes.purpose = ?
              and email_codes.code_hash = ?
              and email_codes.used_at is null
              and email_codes.expires_at > ?
            """,
            (email, purpose, code_hash(email, purpose, code), now),
        ).fetchone()
        if row is None:
            return None
        db.execute(
            "update email_codes set used_at = ? where code_hash = ?",
            (utc_timestamp(), code_hash(email, purpose, code)),
        )
    return dict(row)


def local_user_by_email(email: str) -> dict[str, Any] | None:
    with local_db() as db:
        row = db.execute("select * from users where email = ?", (email,)).fetchone()
    return dict(row) if row else None


def local_mark_email_verified(user_id: str) -> None:
    with local_db() as db:
        db.execute("update users set email_verified = 1 where id = ?", (user_id,))


def public_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(row["id"]),
        "email": str(row["email"]),
    }


def session_payload(user: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(32)
    now = time.time()
    with local_db() as db:
        db.execute(
            "delete from sessions where user_id = ? and expires_at <= ?",
            (str(user["id"]), now),
        )
        db.executemany(
            """
            insert into sessions (token_hash, user_id, kind, expires_at, created_at)
            values (?, ?, ?, ?, ?)
            """,
            [
                (
                    token_hash(access_token),
                    str(user["id"]),
                    "access",
                    now + ACCESS_TOKEN_SECONDS,
                    utc_timestamp(),
                ),
                (
                    token_hash(refresh_token),
                    str(user["id"]),
                    "refresh",
                    now + REFRESH_TOKEN_SECONDS,
                    utc_timestamp(),
                ),
            ],
        )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": ACCESS_TOKEN_SECONDS,
        "user": public_user(user),
    }


def local_create_user(email: str, password: str) -> dict[str, Any]:
    user_id = str(uuid.uuid4())
    salt, digest = hash_password(password)
    try:
        with local_db() as db:
            db.execute(
                """
                insert into users (
                  id, email, password_salt, password_hash, approved, blocked, created_at
                ) values (?, ?, ?, ?, 0, 0, ?)
                """,
                (user_id, email, salt, digest, utc_timestamp()),
            )
    except sqlite3.IntegrityError as exc:
        raise PublicError("Ce compte existe déjà. Connecte-toi avec ton mot de passe.", status=409) from exc
    return {"id": user_id, "email": email}


def local_login_user(email: str, password: str) -> dict[str, Any] | None:
    with local_db() as db:
        row = db.execute(
            "select * from users where email = ?",
            (email,),
        ).fetchone()
        if row is None or not verify_password(password, row["password_salt"], row["password_hash"]):
            return None
        db.execute(
            "update users set last_sign_in_at = ? where id = ?",
            (utc_timestamp(), row["id"]),
        )
    return dict(row)


def validate_email_password(payload: dict[str, Any]) -> tuple[str, str]:
    email = payload.get("email")
    password = payload.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        raise PublicError("E-mail ou mot de passe invalide.")
    email = email.strip().lower()
    if len(email) > 320 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise PublicError("E-mail invalide.")
    if len(password) < 6 or len(password) > 256:
        raise PublicError("Le mot de passe doit faire au moins 6 caractères.")
    return email, password


def local_user_for_access_token(token: str) -> dict[str, Any] | None:
    now = time.time()
    with local_db() as db:
        row = db.execute(
            """
            select users.*
            from sessions
            join users on users.id = sessions.user_id
            where sessions.token_hash = ?
              and sessions.kind = 'access'
              and sessions.expires_at > ?
            """,
            (token_hash(token), now),
        ).fetchone()
    return dict(row) if row else None


def local_refresh_session(refresh_token: str) -> dict[str, Any] | None:
    now = time.time()
    with local_db() as db:
        row = db.execute(
            """
            select users.*
            from sessions
            join users on users.id = sessions.user_id
            where sessions.token_hash = ?
              and sessions.kind = 'refresh'
              and sessions.expires_at > ?
            """,
            (token_hash(refresh_token), now),
        ).fetchone()
        if row is None:
            return None
        db.execute("delete from sessions where token_hash = ?", (token_hash(refresh_token),))
    return session_payload(dict(row))


def local_delete_session(token: str) -> None:
    with local_db() as db:
        db.execute("delete from sessions where token_hash = ?", (token_hash(token),))


def local_admin_profiles() -> list[dict[str, Any]]:
    with local_db() as db:
        rows = db.execute(
            """
            select id, email, email_verified, approved, blocked, created_at, last_sign_in_at
            from users
            order by created_at desc
            """
        ).fetchall()
    return [
        {
            "user_id": row["id"],
            "email": row["email"],
            "email_verified": bool(row["email_verified"]),
            "last_sign_in_at": row["last_sign_in_at"] or "",
            "approved": bool(row["approved"]),
            "blocked": bool(row["blocked"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def local_set_user_access(user_id: str, approved: bool) -> dict[str, Any] | None:
    with local_db() as db:
        db.execute(
            "update users set approved = ?, blocked = ? where id = ?",
            (1 if approved else 0, 0 if approved else 1, user_id),
        )
        row = db.execute(
            """
            select id, email, email_verified, approved, blocked, created_at, last_sign_in_at
            from users where id = ?
            """,
            (user_id,),
        ).fetchone()
    return local_admin_profiles_from_rows([row])[0] if row else None


def local_admin_profiles_from_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [
        {
            "user_id": row["id"],
            "email": row["email"],
            "email_verified": bool(row["email_verified"]),
            "last_sign_in_at": row["last_sign_in_at"] or "",
            "approved": bool(row["approved"]),
            "blocked": bool(row["blocked"]),
            "created_at": row["created_at"],
        }
        for row in rows
        if row is not None
    ]


def local_update_user_password(user_id: str, password: str) -> bool:
    salt, digest = hash_password(password)
    with local_db() as db:
        cursor = db.execute(
            "update users set password_salt = ?, password_hash = ? where id = ?",
            (salt, digest, user_id),
        )
    return cursor.rowcount > 0


def local_delete_user(user_id: str) -> bool:
    with local_db() as db:
        cursor = db.execute("delete from users where id = ?", (user_id,))
    return cursor.rowcount > 0


def normalize_history_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": str(row["job_id"])[:120],
        "user_id": str(row["user_id"])[:40],
        "provider": str(row["provider"])[:16],
        "source_url": str(row["source_url"] or "")[:DIRECT_LINK_LIMIT],
        "title": str(row["title"] or "")[:200],
        "artist": str(row["artist"] or "")[:200],
        "resource_kind": str(row["resource_kind"] or "")[:32],
        "state": str(row["state"])[:24],
        "track_count": int(row["track_count"] or 0),
        "estimated_savings_cents": int(row["estimated_savings_cents"] or 0),
        "created_at": str(row["created_at"] or "")[:40],
        "completed_at": str(row["completed_at"] or "")[:40],
        "delivered_at": str(row["delivered_at"] or "")[:40],
    }


def local_upsert_history(user_id: str, rows: list[dict[str, Any]]) -> None:
    with local_db() as db:
        db.execute("begin immediate")
        for row in rows:
            job_id = str(row.get("job_id", ""))[:120]
            if not job_id:
                continue
            if db.execute("select 1 from library_removed where user_id=? and job_id=?", (user_id, job_id)).fetchone():
                continue
            db.execute(
                """
                insert into download_history (
                  job_id, user_id, provider, source_url, title, artist, resource_kind,
                  state, track_count, estimated_savings_cents, created_at, completed_at,
                  delivered_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(job_id) do update set
                  provider = excluded.provider,
                  source_url = excluded.source_url,
                  title = excluded.title,
                  artist = excluded.artist,
                  resource_kind = excluded.resource_kind,
                  state = excluded.state,
                  track_count = excluded.track_count,
                  estimated_savings_cents = excluded.estimated_savings_cents,
                  created_at = excluded.created_at,
                  completed_at = excluded.completed_at,
                  delivered_at = excluded.delivered_at
                where download_history.user_id = excluded.user_id
                """,
                (
                    job_id,
                    user_id,
                    str(row.get("provider", ""))[:16],
                    str(row.get("source_url", ""))[:DIRECT_LINK_LIMIT],
                    str(row.get("title", ""))[:200],
                    str(row.get("artist", ""))[:200],
                    str(row.get("resource_kind", "track"))[:32],
                    str(row.get("state", ""))[:24],
                    int(row.get("track_count") or 0),
                    int(row.get("estimated_savings_cents") or 0),
                    str(row.get("created_at") or utc_timestamp())[:40],
                    row.get("completed_at") or None,
                    row.get("delivered_at") or None,
                ),
            )


def local_user_history(user_id: str, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
    with local_db() as db:
        rows = db.execute(
            """
            select * from download_history
            where user_id = ?
            order by created_at desc
            limit ? offset ?
            """,
            (user_id, limit, offset),
        ).fetchall()
    return [normalize_history_row(row) for row in rows]


def local_admin_history(user_id: str = "") -> list[dict[str, Any]]:
    with local_db() as db:
        if user_id:
            rows = db.execute(
                """
                select * from download_history
                where user_id = ?
                order by created_at desc
                limit 500
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = db.execute(
                """
                select * from download_history
                order by created_at desc
                limit 500
                """
            ).fetchall()
    return [normalize_history_row(row) for row in rows]


def local_daily_history_rows() -> list[dict[str, Any]]:
    with local_db() as db:
        rows = db.execute(
            """
            select created_at, state, track_count, estimated_savings_cents
            from download_history
            where state = 'done'
            order by created_at desc
            limit 5000
            """
        ).fetchall()
    return [dict(row) for row in rows]


def audio_mime_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix == ".flac":
        return "audio/flac"
    if suffix == ".m4a":
        return "audio/mp4"
    guessed = mimetypes.guess_type(name)[0] or ""
    return guessed if guessed.startswith("audio/") else ""


def cover_mime_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    guessed = mimetypes.guess_type(name)[0] or ""
    return guessed if guessed.startswith("image/") else ""


def embedded_cover_for_audio(path: Path) -> dict[str, Any] | None:
    try:
        audio = MutagenFile(path)
    except Exception:
        return None
    if audio is None:
        return None

    pictures = getattr(audio, "pictures", None)
    if pictures:
        for picture in pictures:
            data = getattr(picture, "data", b"")
            mime_type = str(getattr(picture, "mime", "") or "image/jpeg")
            if data and mime_type.startswith("image/"):
                return {"data": data, "mime_type": mime_type}

    tags = getattr(audio, "tags", None)
    if not tags:
        return None
    for value in tags.values():
        if getattr(value, "FrameID", "") == "APIC" and getattr(value, "data", b""):
            return {
                "data": value.data,
                "mime_type": str(getattr(value, "mime", "") or "image/jpeg"),
            }
    covers = tags.get("covr") if hasattr(tags, "get") else None
    if covers:
        for cover in covers:
            data = bytes(cover)
            image_format = getattr(cover, "imageformat", None)
            mime_type = "image/png" if image_format == 14 else "image/jpeg"
            if data:
                return {"data": data, "mime_type": mime_type}
    return None


def zip_members(path: Path) -> list[zipfile.ZipInfo]:
    try:
        with zipfile.ZipFile(path) as archive:
            return [
                item
                for item in archive.infolist()
                if not item.is_dir() and not Path(item.filename).name.startswith(".")
            ]
    except (OSError, zipfile.BadZipFile):
        return []


def single_zip_audio(path: Path) -> zipfile.ZipInfo | None:
    audio_members = [
        item
        for item in zip_members(path)
        if Path(item.filename).suffix.lower() in musicdl.AUDIO_EXTENSIONS
    ]
    return audio_members[0] if len(audio_members) == 1 else None


def zip_cover(path: Path) -> dict[str, Any] | None:
    image_members = [
        item
        for item in zip_members(path)
        if cover_mime_type(Path(item.filename).name)
    ]
    if not image_members:
        return None
    preferred_names = {"cover", "folder", "front", "album", "artwork"}
    image_members.sort(
        key=lambda item: (
            Path(item.filename).stem.lower() not in preferred_names,
            item.file_size,
            item.filename.lower(),
        )
    )
    member = image_members[0]
    try:
        with zipfile.ZipFile(path) as archive:
            data = archive.read(member)
    except (OSError, zipfile.BadZipFile, KeyError):
        return None
    return {"data": data, "mime_type": cover_mime_type(Path(member.filename).name)}


def delivery_for_job(job_id: str, owner_id: str) -> dict[str, Any] | None:
    try:
        _path, job = find_done_job(job_id)
    except PublicError:
        return None
    if job.get("owner_id") != owner_id:
        return None
    delivery = job.get("delivery")
    if not isinstance(delivery, dict) or not isinstance(delivery.get("path"), str):
        return None
    candidate = (musicdl.DELIVERY_ROOT / delivery["path"]).resolve()
    root = musicdl.DELIVERY_ROOT.resolve()
    if (
        not candidate.is_relative_to(root)
        or not candidate.is_file()
        or candidate.is_symlink()
    ):
        return None
    name = str(delivery.get("name") or candidate.name)
    mime_type = audio_mime_type(name)
    if not mime_type and candidate.suffix.lower() == ".zip":
        member = single_zip_audio(candidate)
        if member:
            name = Path(member.filename).name
            mime_type = audio_mime_type(name)
    return {
        "name": name,
        "size": candidate.stat().st_size,
        "mime_type": mime_type,
        "path": candidate,
    }


def cover_for_job(job_id: str, owner_id: str) -> dict[str, Any] | None:
    delivery = delivery_for_job(job_id, owner_id)
    if not delivery:
        return None
    delivery_path = delivery["path"]
    if delivery_path.suffix.lower() == ".zip":
        return zip_cover(delivery_path)
    embedded = embedded_cover_for_audio(delivery_path)
    if embedded:
        return embedded
    search_dirs = [delivery_path.parent]
    candidates: list[Path] = []
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and not path.is_symlink() and cover_mime_type(path.name):
                candidates.append(path)
    if not candidates:
        return None
    preferred_names = {"cover", "folder", "front", "album", "artwork"}
    candidates.sort(
        key=lambda path: (
            Path(path.stem.lower()).name not in preferred_names,
            path.stat().st_size,
            path.name.lower(),
        )
    )
    path = candidates[0]
    return {"path": path, "mime_type": cover_mime_type(path.name)}


def local_library_tracks(user_id: str) -> list[dict[str, Any]]:
    tracks = []
    analyses = mixer_analysis.summaries(local_db, user_id)
    with local_db() as db:
        removed = {row["job_id"] for row in db.execute(
            "select job_id from library_removed where user_id = ?", (user_id,)
        )}
    tags_by_job = local_track_tag_map(user_id)
    for row in local_user_history(user_id, limit=500, offset=0):
        if row["state"] != "done" or row["job_id"] in removed:
            continue
        delivery = delivery_for_job(row["job_id"], user_id)
        if not delivery:
            continue
        cover = cover_for_job(row["job_id"], user_id)
        tracks.append({
            **row,
            "file_name": delivery["name"],
            "file_size": delivery["size"],
            "streamable": bool(delivery["mime_type"]),
            "cover_url": f"/api/covers/{row['job_id']}" if cover else "",
            "tags": tags_by_job.get(row["job_id"], []),
            "mix_analysis": analyses.get(row["job_id"], {"status": "pending"}),
        })
    return tracks


def local_library_remove_track(user_id: str, job_id: str) -> None:
    if not musicdl.JOB_ID_RE.fullmatch(job_id):
        raise PublicError("Morceau introuvable.", status=404)
    for provider in musicdl.PROVIDERS:
        with musicdl.provider_lock(provider, "enqueue"), local_db() as db:
            db.execute("begin immediate")
            owned = db.execute("select 1 from download_history where user_id=? and job_id=? and provider=?",
                               (user_id, job_id, provider)).fetchone()
            paths = []
            running = False
            for state in musicdl.STATES:
                path = musicdl.QUEUE_ROOT / state / provider / f"{job_id}.json"
                if not path.is_file():
                    continue
                payload = musicdl.load_job(path, expected_provider=provider)
                if payload.get("owner_id") != user_id:
                    raise PublicError("Morceau introuvable.", status=404)
                paths.append(path)
                running |= state == "running"
            if not owned and not paths:
                continue
            for path in paths:
                path.unlink(missing_ok=True)
            # An active worker stops its subprocess and cleans up after it exits.
            if not running or not worker_is_active(provider):
                musicdl.remove_job_files(provider, user_id, job_id)
            db.execute("insert or ignore into library_removed (user_id, job_id) values (?, ?)",
                       (user_id, job_id))
            db.execute("delete from playlist_items where job_id = ? and playlist_id in "
                       "(select id from playlists where user_id = ?)", (job_id, user_id))
            db.execute("delete from tag_items where job_id = ? and tag_id in "
                       "(select id from tags where user_id = ?)", (job_id, user_id))
            db.execute("delete from download_history where user_id=? and job_id=?", (user_id, job_id))
            db.execute("delete from stream_tickets where user_id=? and job_id=?", (user_id, job_id))
            return
    with local_db() as db:
        if db.execute("select 1 from library_removed where user_id=? and job_id=?", (user_id, job_id)).fetchone():
            return
    raise PublicError("Morceau introuvable.", status=404)


def normalize_playlist_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "folder_id": row["folder_id"],
        "id": str(row["id"]),
        "name": str(row["name"])[:80],
        "created_at": str(row["created_at"] or "")[:40],
        "track_ids": [
            value for value in str(row["track_ids"] or "").split("\n") if value
        ],
    }


def local_playlists(user_id: str) -> list[dict[str, Any]]:
    with local_db() as db:
        rows = db.execute(
            """
            select
              playlists.id,
              playlists.name,
              playlists.created_at,
              playlist_folder_items.folder_id,
              (select group_concat(job_id, char(10)) from
                (select job_id from playlist_items where playlist_id=playlists.id
                 order by created_at, rowid)) as track_ids
            from playlists
            left join playlist_folder_items on playlist_folder_items.playlist_id = playlists.id
            where playlists.user_id = ?
            group by playlists.id
            order by playlists.created_at desc
            """,
            (user_id,),
        ).fetchall()
    return [normalize_playlist_row(row) for row in rows]


def normalize_tag_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": str(row["name"])[:40],
        "color": str(row["color"] or "#665cff")[:16],
        "created_at": str(row["created_at"] or "")[:40],
        "track_ids": [
            value for value in str(row["track_ids"] or "").split("\n") if value
        ],
    }


def local_tags(user_id: str) -> list[dict[str, Any]]:
    with local_db() as db:
        rows = db.execute(
            """
            select
              tags.id,
              tags.name,
              tags.color,
              tags.created_at,
              (select group_concat(job_id, char(10)) from
                (select job_id from tag_items where tag_id=tags.id
                 order by created_at, rowid)) as track_ids
            from tags
            where tags.user_id = ?
            group by tags.id
            order by tags.created_at desc
            """,
            (user_id,),
        ).fetchall()
    return [normalize_tag_row(row) for row in rows]


def local_track_tag_map(user_id: str) -> dict[str, list[dict[str, str]]]:
    with local_db() as db:
        rows = db.execute(
            """
            select tag_items.job_id, tags.id, tags.name, tags.color
            from tag_items
            join tags on tags.id = tag_items.tag_id
            where tags.user_id = ?
            order by tag_items.created_at desc
            """,
            (user_id,),
        ).fetchall()
    tag_map: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        tag_map.setdefault(str(row["job_id"]), []).append({
            "id": str(row["id"]),
            "name": str(row["name"])[:40],
            "color": str(row["color"] or "#665cff")[:16],
        })
    return tag_map


def local_create_playlist(user_id: str, name: str) -> dict[str, Any]:
    name = re.sub(r"\s+", " ", name.strip())[:80]
    if not name:
        raise PublicError("Nom de playlist invalide.")
    playlist_id = str(uuid.uuid4())
    created_at = utc_timestamp()
    with local_db() as db:
        db.execute(
            "insert into playlists (id, user_id, name, created_at) values (?, ?, ?, ?)",
            (playlist_id, user_id, name, created_at),
        )
    return {"id": playlist_id, "name": name, "created_at": created_at, "track_ids": []}


TAG_COLORS = ["#665cff", "#14866d", "#d14d72", "#9b6b00", "#2878b8", "#6f55a3"]


def normalize_tag_color(color: Any, seed: str) -> str:
    if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color.strip()):
        return color.strip().lower()
    return TAG_COLORS[sum(seed.encode()) % len(TAG_COLORS)]


def local_create_tag(user_id: str, name: str, color: Any = "") -> dict[str, Any]:
    name = re.sub(r"\s+", " ", name.strip())[:40]
    if not name:
        raise PublicError("Nom de tag invalide.")
    tag_id = str(uuid.uuid4())
    created_at = utc_timestamp()
    tag_color = normalize_tag_color(color, name)
    with local_db() as db:
        db.execute(
            "insert into tags (id, user_id, name, color, created_at) values (?, ?, ?, ?, ?)",
            (tag_id, user_id, name, tag_color, created_at),
        )
    return {
        "id": tag_id,
        "name": name,
        "color": tag_color,
        "created_at": created_at,
        "track_ids": [],
    }


def local_rename_collection(user_id: str, collection: str, item_id: str, name: Any) -> str:
    if collection not in {"playlists", "tags"}:
        raise PublicError("Collection inconnue.", status=404)
    limit = 80 if collection == "playlists" else 40
    if not isinstance(name, str):
        raise PublicError("Nom invalide.")
    name = re.sub(r"\s+", " ", name.strip())
    if not name or len(name) > limit or "\x00" in name:
        raise PublicError(f"Le nom doit contenir entre 1 et {limit} caractères.")
    with local_db() as db:
        cursor = db.execute(f"update {collection} set name = ? where id = ? and user_id = ?",
                            (name, item_id, user_id))
        if not cursor.rowcount:
            raise PublicError("Playlist ou tag introuvable.", status=404)
    return name


def local_delete_playlist(user_id: str, playlist_id: str) -> bool:
    with local_db() as db:
        cursor = db.execute(
            "delete from playlists where id = ? and user_id = ?",
            (playlist_id, user_id),
        )
    return cursor.rowcount > 0


def local_delete_tag(user_id: str, tag_id: str) -> bool:
    with local_db() as db:
        cursor = db.execute(
            "delete from tags where id = ? and user_id = ?",
            (tag_id, user_id),
        )
    return cursor.rowcount > 0


def local_playlist_has_owner(db: sqlite3.Connection, playlist_id: str, user_id: str) -> bool:
    row = db.execute(
        "select 1 from playlists where id = ? and user_id = ?",
        (playlist_id, user_id),
    ).fetchone()
    return row is not None


def local_tag_has_owner(db: sqlite3.Connection, tag_id: str, user_id: str) -> bool:
    row = db.execute(
        "select 1 from tags where id = ? and user_id = ?",
        (tag_id, user_id),
    ).fetchone()
    return row is not None


def local_playlist_add_track(user_id: str, playlist_id: str, job_id: str) -> None:
    if not musicdl.JOB_ID_RE.fullmatch(job_id):
        raise PublicError("Morceau invalide.", status=404)
    if not delivery_for_job(job_id, user_id):
        raise PublicError("Morceau introuvable.", status=404)
    with local_db() as db:
        if not local_playlist_has_owner(db, playlist_id, user_id):
            raise PublicError("Playlist introuvable.", status=404)
        db.execute(
            """
            insert or ignore into playlist_items (playlist_id, job_id, created_at)
            values (?, ?, ?)
            """,
            (playlist_id, job_id, utc_timestamp()),
        )


def local_tag_add_track(user_id: str, tag_id: str, job_id: str) -> None:
    if not musicdl.JOB_ID_RE.fullmatch(job_id):
        raise PublicError("Morceau invalide.", status=404)
    if not delivery_for_job(job_id, user_id):
        raise PublicError("Morceau introuvable.", status=404)
    with local_db() as db:
        if not local_tag_has_owner(db, tag_id, user_id):
            raise PublicError("Tag introuvable.", status=404)
        db.execute(
            """
            insert or ignore into tag_items (tag_id, job_id, created_at)
            values (?, ?, ?)
            """,
            (tag_id, job_id, utc_timestamp()),
        )


def local_playlist_remove_track(user_id: str, playlist_id: str, job_id: str) -> None:
    with local_db() as db:
        if not local_playlist_has_owner(db, playlist_id, user_id):
            raise PublicError("Playlist introuvable.", status=404)
        db.execute(
            "delete from playlist_items where playlist_id = ? and job_id = ?",
            (playlist_id, job_id),
        )


def local_tag_remove_track(user_id: str, tag_id: str, job_id: str) -> None:
    with local_db() as db:
        if not local_tag_has_owner(db, tag_id, user_id):
            raise PublicError("Tag introuvable.", status=404)
        db.execute(
            "delete from tag_items where tag_id = ? and job_id = ?",
            (tag_id, job_id),
        )


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
    user = await asyncio.to_thread(local_user_for_access_token, token)
    if user is None:
        raise PublicError("Session expirée. Reconnectez-vous.", status=401)
    if not user.get("email_verified"):
        raise PublicError("E-mail non vérifié. Entre le code reçu par e-mail.", status=403)
    if not user.get("approved"):
        if user.get("blocked"):
            raise PublicError("Compte refusé. Contacte l'administrateur si tu penses que c'est une erreur.", status=403)
        raise PublicError("Compte en attente d'approbation. Contacte l'administrateur pour activer ton accès.", status=403)
    cache[cache_key] = (time.monotonic() + AUTH_CACHE_SECONDS, user)
    return user


async def authenticate_admin_request(request: web.Request) -> dict[str, str]:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise PublicError("Connexion admin requise.", status=401)
    token = authorization[7:].strip()
    if not token:
        raise PublicError("Session admin invalide.", status=401)
    sessions = request.app[ADMIN_SESSIONS_KEY]
    expires_at = sessions.get(token)
    if not isinstance(expires_at, (int, float)) or expires_at < time.monotonic():
        sessions.pop(token, None)
        raise PublicError("Session admin expirée.", status=401)
    return {"email": ADMIN_EMAIL}


@web.middleware
async def api_middleware(
    request: web.Request, handler: Callable[[web.Request], Any]
) -> web.StreamResponse:
    try:
        _check_local_host(request)
        public_paths = {
            "/",
            "/app.js",
            "/style.css",
            "/vendor/player-icons.js",
            "/vendor/mixer-icons.js",
            "/api/health",
            "/api/config",
            "/api/signup",
            "/api/login",
            "/api/email/verify",
            "/api/email/resend",
            "/api/password-reset/request",
            "/api/password-reset/confirm",
            "/api/session/refresh",
            "/api/admin/login",
        }
        if request.path.startswith("/api/admin/") and request.path not in public_paths:
            request["admin"] = await authenticate_admin_request(request)
        elif (
            request.path not in public_paths
            and not request.path.startswith("/api/files/")
            and not request.path.startswith("/api/stream/")
            and not request.path.startswith("/api/playlist-files/")
        ):
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


def tidal_image_url(image_id: Any, *, size: int = 320) -> str:
    value = str(image_id or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F-]{32,36}", value):
        return ""
    path = value.replace("-", "/")
    return f"https://resources.tidal.com/images/{path}/{size}x{size}.jpg"


def first_image_url(value: Any) -> str:
    if isinstance(value, str):
        url = value.strip()
        if url.startswith("//"):
            url = f"https:{url}"
        if url.startswith("https://"):
            return (
                url.replace("{w}", "320")
                .replace("{h}", "320")
                .replace("{width}", "320")
                .replace("{height}", "320")
            )
    if isinstance(value, dict):
        for key in ("url", "uri", "dynamic_uri", "large", "small", "thumbnail"):
            result = first_image_url(value.get(key))
            if result:
                return result
        for nested in value.values():
            result = first_image_url(nested)
            if result:
                return result
    if isinstance(value, list):
        for item in value:
            result = first_image_url(item)
            if result:
                return result
    return ""


def normalized_result(
    *,
    item_id: str,
    provider: str,
    platform: str,
    title: str,
    artist: str,
    album: str,
    label: str = "",
    mix: str = "",
    quality: str = "",
    bpm: str = "",
    key: str = "",
    duration: str = "",
    cover_url: str = "",
    source_url: str,
) -> dict[str, Any]:
    parts = [part for part in (album, label, mix, quality, bpm, key, duration) if part]
    return {
        "id": f"{provider}:track:{item_id}",
        "provider": provider,
        "platform": platform,
        "kind": "track",
        "title": title,
        "artist": artist,
        "album": album,
        "label": label,
        "mix": mix,
        "quality": quality,
        "bpm": bpm,
        "key": key,
        "duration": duration,
        "details": " · ".join(parts),
        "cover_url": cover_url,
        "source_url": source_url,
        "download_url": source_url,
    }


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
        album_title = str(album.get("title", "")).strip()
        quality = str(track.get("audioQuality", "")).replace("_", " ").strip()
        duration = format_duration(track.get("duration"))
        cover_url = tidal_image_url(album.get("cover")) or first_image_url(album)
        download_url = f"https://listen.tidal.com/track/{track_id}"
        normalized.append(
            normalized_result(
                item_id=track_id,
                provider="tiddl",
                platform="TIDAL",
                title=title,
                artist=artist,
                album=album_title,
                quality=quality,
                duration=duration,
                cover_url=cover_url,
                source_url=download_url,
            )
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
        album_title = str(release.get("name", "")).strip()
        label = ""
        label_payload = track.get("label") if isinstance(track.get("label"), dict) else {}
        release_label = release.get("label") if isinstance(release.get("label"), dict) else {}
        for candidate in (label_payload, release_label):
            if candidate.get("name"):
                label = str(candidate["name"]).strip()
                break
        bpm = track.get("bpm")
        bpm_label = f"{int(bpm)} BPM" if isinstance(bpm, (int, float)) and bpm else ""
        key = track.get("key") if isinstance(track.get("key"), dict) else {}
        key_label = str(key.get("name", "")).strip()
        cover_url = first_image_url(track.get("image")) or first_image_url(release.get("image")) or first_image_url(track.get("images")) or first_image_url(release.get("images"))

        safe_slug = quote(slug, safe="-")
        download_url = f"https://www.beatport.com/track/{safe_slug}/{track_id}"
        normalized.append(
            normalized_result(
                item_id=track_id,
                provider="beatport",
                platform="Beatport",
                title=title,
                artist=", ".join(artist_names),
                album=album_title,
                label=label,
                mix=mix_name if mix_name.lower() != "original mix" else "",
                bpm=bpm_label,
                key=key_label,
                cover_url=cover_url,
                source_url=download_url,
            )
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
        album_title = str(album.get("title", "")).strip()
        quality = ""
        if track.get("hires_streamable") is True:
            bit_depth = track.get("maximum_bit_depth")
            sampling_rate = track.get("maximum_sampling_rate")
            if bit_depth and sampling_rate:
                quality = f"Hi-Res {bit_depth}-bit/{sampling_rate} kHz"
            else:
                quality = "Hi-Res"
        duration = format_duration(track.get("duration"))
        label_payload = album.get("label") if isinstance(album.get("label"), dict) else {}
        label = str(label_payload.get("name", "")).strip()
        cover_url = first_image_url(album.get("image")) or first_image_url(track.get("image"))
        download_url = f"https://play.qobuz.com/track/{quote(track_id, safe='')}"
        normalized.append(
            normalized_result(
                item_id=track_id,
                provider="qobuz",
                platform="Qobuz",
                title=title,
                artist=artist,
                album=album_title,
                label=label,
                quality=quality,
                duration=duration,
                cover_url=cover_url,
                source_url=download_url,
            )
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


def beatport_helper_search(query: str, *, proxy: str = "", collection: str = "") -> dict[str, Any]:
    command = [BEATPORT_SEARCH_BINARY]
    if proxy:
        command.extend(["--proxy", proxy])
    if collection:
        command.extend(["--collection", collection])
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


def probe_provider_connection(provider: str) -> dict[str, str]:
    def result(state: str, message: str) -> dict[str, str]:
        return {"state": state, "message": message}

    try:
        if provider == "tiddl":
            auth = refresh_tidal_auth(force=False)
            response = requests.get(
                f"https://api.tidal.com/v1/users/{quote(str(auth['user_id']), safe='')}/subscription",
                headers={"Authorization": f"Bearer {auth['token']}", "Accept": "application/json"},
                timeout=(3, 8), allow_redirects=False,
            )
        elif provider == "qobuz":
            config = musicdl.read_qobuz_config().defaults()
            if any(not config.get(key) for key in ("user_id", "user_auth_token", "app_id")):
                return result("inactive", "Connexion Qobuz non configurée.")
            response = requests.get(
                "https://www.qobuz.com/api.json/0.2/user/login",
                params={key: config[key] for key in ("user_id", "user_auth_token", "app_id")},
                headers={"X-App-Id": config["app_id"], "Accept": "application/json"},
                timeout=(3, 8), allow_redirects=False,
            )
        else:
            if configured_beatport_proxy():
                return result("unknown", "Moteur démarré ; vérification de connexion indisponible avec le proxy.")
            token = valid_beatport_token()
            if not token:
                return result("unknown", "Moteur démarré ; connexion à vérifier lors de la prochaine utilisation.")
            if beatport_api_search("music", token) is None:
                return result("inactive", "Connexion Beatport refusée. Reconnexion nécessaire.")
            return result("active", "Moteur démarré et connexion API vérifiée.")

        if response.status_code in {401, 403}:
            return result("inactive", "Connexion refusée. Reconnexion du compte nécessaire.")
        if response.status_code != 200:
            return result("unknown", f"Plateforme momentanément inaccessible (HTTP {response.status_code}).")
        data = response.json()
        if not isinstance(data, dict):
            return result("unknown", "Réponse de vérification invalide.")
        if provider == "tiddl":
            subscription = data.get("subscription") or {}
            kind = str(subscription.get("type") or "").upper()
            if kind == "FREE":
                return result("inactive", "Compte TIDAL FREE : abonnement requis pour accéder aux sons.")
            if not kind or not data.get("status"):
                return result("unknown", "Droits de l’abonnement TIDAL non vérifiables.")
            if str(data["status"]).upper() != "ACTIVE":
                return result("inactive", "Abonnement TIDAL non actif.")
        else:
            if not isinstance(data.get("user"), dict):
                return result("unknown", "Réponse de vérification Qobuz invalide.")
            parameters = ((data.get("user") or {}).get("credential") or {}).get("parameters")
            if not parameters:
                return result("inactive", "Compte Qobuz sans droits de streaming.")
        return result("active", "Moteur démarré, connexion et abonnement vérifiés.")
    except FileNotFoundError:
        return result("inactive", "Connexion non configurée.")
    except (OSError, RuntimeError, PublicError, ValueError, KeyError, TypeError, AttributeError, requests.RequestException):
        # Never expose exception text: Qobuz's request URLs include credentials.
        return result("unknown", "Connexion non vérifiable pour le moment.")


async def provider_statuses(app: web.Application, workers: dict[str, bool]) -> dict[str, Any]:
    cache = app[STATUS_CACHE_KEY]
    values = cache.setdefault("providers", {})
    tasks = cache.setdefault("tasks", {})

    async def check(provider: str) -> None:
        try:
            value = await asyncio.to_thread(probe_provider_connection, provider)
            values[provider] = {**value, "checked_at": utc_timestamp(), "monotonic": time.monotonic()}
        finally:
            tasks.pop(provider, None)

    statuses = {}
    for provider in musicdl.PROVIDERS:
        if not workers.get(provider):
            values.pop(provider, None)
            statuses[provider] = {"state": "inactive", "message": "Moteur de téléchargement arrêté.", "worker_running": False}
            continue
        value = values.get(provider)
        fresh = value and time.monotonic() - value["monotonic"] < PROVIDER_STATUS_SECONDS
        if not fresh and provider not in tasks:
            tasks[provider] = asyncio.create_task(check(provider))
        statuses[provider] = ({key: value[key] for key in ("state", "message", "checked_at")} if fresh else
                              {"state": "loading", "message": "Vérification de la connexion et des droits en cours."})
        statuses[provider]["worker_running"] = True
    return statuses


async def provider_status_context(app: web.Application):
    yield
    tasks = list(app[STATUS_CACHE_KEY].get("tasks", {}).values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def status_handler(request: web.Request) -> web.Response:
    snapshot = await asyncio.to_thread(queue_snapshot, request["user"]["id"])
    snapshot["services"] = await provider_statuses(request.app, snapshot["active"])
    return web.json_response(snapshot)


async def admin_login_handler(request: web.Request) -> web.Response:
    if not ADMIN_PASSWORD:
        raise PublicError("Admin non configuré.", status=503)
    payload = await read_json_object(request)
    email = payload.get("email")
    password = payload.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        raise PublicError("Identifiants admin invalides.", status=401)
    valid_email = secrets.compare_digest(email.strip(), ADMIN_EMAIL)
    valid_password = secrets.compare_digest(password, ADMIN_PASSWORD)
    if not valid_email or not valid_password:
        raise PublicError("Identifiants admin invalides.", status=401)
    token = secrets.token_urlsafe(32)
    request.app[ADMIN_SESSIONS_KEY][token] = time.monotonic() + ADMIN_SESSION_SECONDS
    return web.json_response({"access_token": token, "expires_in": ADMIN_SESSION_SECONDS})


async def signup_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    email, password = validate_email_password(payload)
    user = await asyncio.to_thread(local_create_user, email, password)
    await asyncio.to_thread(send_verification_code, user["id"], email)
    return web.json_response(
        {
            "message": "Compte créé. Un code de vérification a été envoyé par e-mail.",
            "pending": True,
            "needs_email_verification": True,
        },
        status=201,
    )


async def login_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    email = payload.get("email")
    password = payload.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        raise PublicError("Identifiants invalides.", status=401)
    user = await asyncio.to_thread(local_login_user, email.strip().lower(), password)
    if user is None:
        raise PublicError("Identifiants invalides.", status=401)
    if not user.get("email_verified"):
        raise PublicError("E-mail non vérifié. Entre le code reçu par e-mail.", status=403)
    if user.get("blocked"):
        raise PublicError("Compte refusé. Contacte l'administrateur si tu penses que c'est une erreur.", status=403)
    if not user.get("approved"):
        raise PublicError("Compte en attente d'approbation. Contacte l'administrateur pour activer ton accès.", status=403)
    return web.json_response(await asyncio.to_thread(session_payload, user))


async def email_verify_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    email = payload.get("email")
    code = payload.get("code")
    if not isinstance(email, str) or not isinstance(code, str):
        raise PublicError("Code invalide.")
    email = email.strip().lower()
    code = code.strip()
    if not re.fullmatch(r"\d{6}", code):
        raise PublicError("Code invalide.")
    user = await asyncio.to_thread(consume_email_code, email, "verify_email", code)
    if user is None:
        raise PublicError("Code invalide ou expiré.", status=400)
    await asyncio.to_thread(local_mark_email_verified, user["id"])
    return web.json_response({
        "message": "E-mail vérifié. Ton compte est en attente d'approbation.",
        "verified": True,
    })


async def email_resend_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    email = payload.get("email")
    if not isinstance(email, str):
        raise PublicError("E-mail invalide.")
    email = email.strip().lower()
    user = await asyncio.to_thread(local_user_by_email, email)
    if user and not user.get("email_verified"):
        await asyncio.to_thread(send_verification_code, user["id"], email)
    return web.json_response({
        "message": "Si ce compte existe, un code vient d'être envoyé.",
    })


async def password_reset_request_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    email = payload.get("email")
    if not isinstance(email, str):
        raise PublicError("E-mail invalide.")
    email = email.strip().lower()
    user = await asyncio.to_thread(local_user_by_email, email)
    if user:
        await asyncio.to_thread(send_password_reset_code, user["id"], email)
    return web.json_response({
        "message": "Si ce compte existe, un code de réinitialisation vient d'être envoyé.",
    })


async def password_reset_confirm_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    email, password = validate_email_password(payload)
    code = payload.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code.strip()):
        raise PublicError("Code invalide.")
    user = await asyncio.to_thread(
        consume_email_code,
        email,
        "password_reset",
        code.strip(),
    )
    if user is None:
        raise PublicError("Code invalide ou expiré.", status=400)
    await asyncio.to_thread(local_update_user_password, user["id"], password)
    await asyncio.to_thread(local_mark_email_verified, user["id"])
    return web.json_response({"message": "Mot de passe mis à jour."})


async def refresh_session_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise PublicError("Session expirée. Reconnecte-toi.", status=401)
    session = await asyncio.to_thread(local_refresh_session, refresh_token)
    if session is None:
        raise PublicError("Session expirée. Reconnecte-toi.", status=401)
    return web.json_response(session)


async def logout_handler(request: web.Request) -> web.Response:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        await asyncio.to_thread(local_delete_session, authorization[7:].strip())
    return web.json_response({"ok": True})


async def history_sync_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    rows = payload.get("history")
    if not isinstance(rows, list):
        raise PublicError("Historique invalide.")
    await asyncio.to_thread(local_upsert_history, request["user"]["id"], rows[:1000])
    return web.json_response({"ok": True})


async def history_handler(request: web.Request) -> web.Response:
    try:
        limit = min(500, max(1, int(request.query.get("limit", "500"))))
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError as exc:
        raise PublicError("Pagination invalide.") from exc
    rows = await asyncio.to_thread(
        local_user_history,
        request["user"]["id"],
        limit=limit,
        offset=offset,
    )
    return web.json_response({"history": rows})


def read_import_source(url: str) -> dict[str, Any]:
    provider, identifier, canonical = playlist_sources.playlist_source(url)
    if provider == "spotify":
        payload = playlist_sources.spotify_playlist(identifier)
    elif provider == "soundcloud":
        payload = playlist_sources.soundcloud_playlist(canonical)
    elif provider == "applemusic":
        payload = playlist_sources.apple_music_playlist(canonical)
    else:
        kind, resource_id = identifier.split(":")
        source = beatport_helper_search(resource_id, proxy=configured_beatport_proxy(), collection=kind)
        payload = {"name": source.get("name", "Beatport"), "tracks": []}
        for item in source.get("tracks", []):
            normalized = normalize_beatport_results({"tracks": [item]})
            if not normalized:
                raise playlist_sources.PlaylistError("Un titre Beatport est inaccessible.")
            payload["tracks"].append(normalized[0])
    if not payload["tracks"] or len(payload["tracks"]) > playlist_sources.MAX_TRACKS:
        raise playlist_sources.PlaylistError("La playlist doit contenir entre 1 et 500 titres accessibles.")
    return payload


def local_imports(user_id: str) -> list[dict[str, Any]]:
    with local_db() as db:
        rows = db.execute(
            "select i.*, p.name from playlist_imports i join playlists p on p.id = i.playlist_id "
            "where i.user_id = ? order by i.created_at desc limit 20", (user_id,)
        ).fetchall()
    return [{"id": r["id"], "playlist_id": r["playlist_id"], "name": r["name"], "state": r["state"],
             "total": len(json.loads(r["tracks"])), "processed": r["processed"], "queued": r["queued"],
             "failures": json.loads(r["failures"]), "error": r["error"]} for r in rows]


async def playlist_import_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    try:
        provider, _, url = playlist_sources.playlist_source(payload.get("url"))
    except (playlist_sources.PlaylistError, ValueError) as exc:
        raise PublicError(str(exc)) from exc
    user_id = request["user"]["id"]
    with local_db() as db:
        existing = db.execute("select id, playlist_id from playlist_imports where user_id = ? "
                              "and source_url = ? and state in ('pending', 'running')", (user_id, url)).fetchone()
        if existing:
            return web.json_response(dict(existing), status=202)
        count = db.execute("select count(*) from playlist_imports where user_id = ? "
                           "and state in ('pending', 'running')", (user_id,)).fetchone()[0]
        if count >= 3:
            raise PublicError("Trois imports sont deja en cours. Attendez leur fin.", status=429)
        import_id, playlist_id = str(uuid.uuid4()), str(uuid.uuid4())
        db.execute("insert into playlists (id, user_id, name, created_at) values (?, ?, ?, ?)",
                   (playlist_id, user_id, f"Import {playlist_sources.SOURCE_LABELS.get(provider, provider.title())}", utc_timestamp()))
        db.execute("insert into playlist_imports (id, user_id, playlist_id, source_url, created_at) "
                   "values (?, ?, ?, ?, ?)", (import_id, user_id, playlist_id, url, utc_timestamp()))
    return web.json_response({"id": import_id, "playlist_id": playlist_id}, status=202)


def enqueue_import_track(record: dict[str, Any], result: dict[str, Any]) -> str:
    provider = result["provider"]
    url = musicdl.validate_url(provider, result["download_url"])
    owner = record["user_id"]
    if resource_kind(provider, url) != "track":
        raise PublicError("La correspondance n'est pas un morceau.")
    # Serialize enqueue and playlist membership so a restart reuses the same job.
    with musicdl.provider_lock(provider, "enqueue"), local_db() as db:
        if not local_playlist_has_owner(db, record["playlist_id"], owner):
            raise PublicError("La playlist a ete supprimee.")
        job_id = existing_job(provider, url, owner)
        if not job_id:
            for path in (musicdl.QUEUE_ROOT / "done" / provider).glob("*.json"):
                try:
                    job = json.loads(path.read_text())
                    if job.get("owner_id") == owner and url in job.get("urls", []) and delivery_for_job(path.stem, owner):
                        job_id = path.stem
                        break
                except (OSError, ValueError):
                    continue
        if not job_id:
            job_id = musicdl.enqueue(provider, [url], owner_id=owner, ephemeral=True,
                                     metadata={"title": result["title"][:200], "artist": result.get("artist", "")[:200], "kind": "track"})
        db.execute("delete from library_removed where user_id = ? and job_id = ?", (owner, job_id))
        db.execute("insert or ignore into playlist_items (playlist_id, job_id, created_at) values (?, ?, ?)",
                   (record["playlist_id"], job_id, utc_timestamp()))
    return job_id


async def process_playlist_import(app: web.Application, record: dict[str, Any]) -> None:
    tracks = json.loads(record["tracks"])
    source_warning = record["error"]
    if not tracks:
        payload = await asyncio.to_thread(read_import_source, record["source_url"])
        tracks = payload["tracks"]
        source_warning = payload.get("warning", "")
        with local_db() as db:
            provider = playlist_sources.playlist_source(record["source_url"])[0]
            db.execute("update playlists set name = ? where id = ? and name = ?",
                       (payload["name"][:80], record["playlist_id"], f"Import {playlist_sources.SOURCE_LABELS.get(provider, provider.title())}"))
            db.execute("update playlist_imports set tracks = ?, error = ?, state = 'running' where id = ?",
                       (json.dumps(tracks), source_warning, record["id"]))
    failures = json.loads(record["failures"])
    queued = record["queued"]
    for index in range(record["processed"], len(tracks)):
        with local_db() as db:
            if not db.execute("select 1 from playlist_imports where id = ?", (record["id"],)).fetchone():
                return
        track = tracks[index]
        result = track if track.get("provider") == "beatport" else None
        errors = []
        if result is None:
            for provider, search in (("tiddl", search_tidal), ("qobuz", search_qobuz), ("beatport", search_beatport)):
                if not worker_is_active(provider):
                    continue
                try:
                    async with app[SEARCH_SEMAPHORES_KEY][provider]:
                        candidates = await asyncio.to_thread(search, f"{track.get('artist', '')} {track['title']}"[:SEARCH_TEXT_LIMIT])
                    result = playlist_sources.matching_track(track, candidates)
                    if result:
                        break
                except SourceError as exc:
                    errors.append(str(exc))
        if result:
            try:
                if not worker_is_active(result["provider"]):
                    raise PublicError("La source de telechargement est arretee.")
                await asyncio.to_thread(enqueue_import_track, record, result)
                queued += 1
            except (PublicError, ValueError) as exc:
                failures.append({"title": track["title"], "artist": track.get("artist", ""), "reason": str(exc)})
        else:
            failures.append({"title": track["title"], "artist": track.get("artist", ""),
                             "reason": errors[0] if errors else "Aucune correspondance fiable sur les sources disponibles."})
        with local_db() as db:
            db.execute("update playlist_imports set processed = ?, queued = ?, failures = ? where id = ?",
                       (index + 1, queued, json.dumps(failures), record["id"]))
        await asyncio.sleep(0.3)
    with local_db() as db:
        db.execute("update playlist_imports set state = ? where id = ?",
                   ("partial" if failures or source_warning else "done", record["id"]))


async def playlist_import_worker(app: web.Application) -> None:
    while True:
        with local_db() as db:
            row = db.execute("select * from playlist_imports where state in ('pending', 'running') "
                             "order by created_at limit 1").fetchone()
        if row:
            try:
                await process_playlist_import(app, dict(row))
            except Exception as exc:
                message = str(exc) if isinstance(exc, (PublicError, playlist_sources.PlaylistError)) else "Import interrompu par une erreur interne."
                LOGGER.warning("playlist import failed: %s", type(exc).__name__)
                with local_db() as db:
                    db.execute("update playlist_imports set state = 'failed', error = ? where id = ?",
                               (message[:400], row["id"]))
        await asyncio.sleep(2)


async def playlist_import_context(app: web.Application):
    task = asyncio.create_task(playlist_import_worker(app))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def library_track_delete_handler(request: web.Request) -> web.Response:
    await asyncio.to_thread(local_library_remove_track, request["user"]["id"],
                            request.match_info.get("job_id", ""))
    return web.json_response({"removed": True})


async def library_handler(request: web.Request) -> web.Response:
    user_id = request["user"]["id"]
    tracks, playlists, tags = await asyncio.gather(
        asyncio.to_thread(local_library_tracks, user_id),
        asyncio.to_thread(local_playlists, user_id),
        asyncio.to_thread(local_tags, user_id),
    )
    imports = await asyncio.to_thread(local_imports, user_id)
    with local_db() as db:
        folders = [dict(row) for row in db.execute(
            "select id, name, collapsed from playlist_folders where user_id = ? order by name, id", (user_id,))]
        orders = {row["scope"]: json.loads(row["track_ids"]) for row in db.execute(
            "select scope, track_ids from library_orders where user_id=?", (user_id,))}
    return web.json_response({"tracks": tracks, "playlists": playlists, "tags": tags, "imports": imports, "folders": folders, "orders": orders})


def local_save_library_order(user_id: str, scope: Any, track_ids: Any) -> None:
    if not isinstance(scope, str) or len(scope) > 80:
        raise PublicError("Collection invalide.")
    kind, _, collection_id = scope.partition(":")
    tables = {"playlist": "playlists", "tag": "tags", "folder": "playlist_folders"}
    if scope != "library:" and (kind not in tables or not collection_id):
        raise PublicError("Collection invalide.")
    if (not isinstance(track_ids, list) or len(track_ids) > 5000
            or any(not isinstance(job, str) or not musicdl.JOB_ID_RE.fullmatch(job) for job in track_ids)
            or len(set(track_ids)) != len(track_ids)):
        raise PublicError("Ordre des morceaux invalide.")
    queued = {job["id"] for job in queue_snapshot(user_id)["recent"]}
    with local_db() as db:
        db.execute("begin immediate")
        if kind in tables and not db.execute(
                f"select 1 from {tables[kind]} where id=? and user_id=?", (collection_id, user_id)).fetchone():
            raise PublicError("Collection introuvable.", status=404)
        owned = {row["job_id"] for row in db.execute("select job_id from download_history where user_id=?", (user_id,))} | queued
        removed = {row["job_id"] for row in db.execute("select job_id from library_removed where user_id=?", (user_id,))}
        if kind in {"playlist", "tag"}:
            items, key = ("playlist_items", "playlist_id") if kind == "playlist" else ("tag_items", "tag_id")
            owned &= {row["job_id"] for row in db.execute(f"select job_id from {items} where {key}=?", (collection_id,))}
        elif kind == "folder":
            owned &= {row["job_id"] for row in db.execute(
                "select i.job_id from playlist_items i join playlist_folder_items f on f.playlist_id=i.playlist_id "
                "join playlists p on p.id=i.playlist_id where f.folder_id=? and p.user_id=?", (collection_id, user_id))}
        if not set(track_ids) <= owned - removed:
            raise PublicError("La liste a changé. Actualisez-la avant de déplacer ce morceau.", status=409)
        db.execute("insert into library_orders(user_id,scope,track_ids) values (?,?,?) "
                   "on conflict(user_id,scope) do update set track_ids=excluded.track_ids",
                   (user_id, scope, json.dumps(track_ids)))


async def library_order_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    await asyncio.to_thread(local_save_library_order, request["user"]["id"], payload.get("scope"), payload.get("track_ids"))
    return web.json_response({"saved": True})


def local_collection_export(user_id: str, collection_id: str, *, tag: bool = False) -> dict[str, Any]:
    table, items, key, label = ("tags", "tag_items", "tag_id", "Tag") if tag else ("playlists", "playlist_items", "playlist_id", "Playlist")
    with local_db() as db:
        collection = db.execute(f"select name from {table} where id=? and user_id=?",
                                (collection_id, user_id)).fetchone()
        if not collection:
            raise PublicError(f"{label} introuvable.", status=404)
        rows = db.execute(f"""select p.job_id, h.source_url, h.state from {items} p
          left join download_history h on h.job_id=p.job_id and h.user_id=?
          where p.{key}=? order by p.created_at, p.rowid""",
                          (user_id, collection_id)).fetchall()
        order = db.execute("select track_ids from library_orders where user_id=? and scope=?",
                           (user_id, f"{'tag' if tag else 'playlist'}:{collection_id}")).fetchone()
        if order:
            ranks = {job: i for i, job in enumerate(json.loads(order["track_ids"]))}
            rows.sort(key=lambda row: ranks.get(row["job_id"], len(ranks)))
    links = []
    for row in rows:
        url = str(row["source_url"] or "").strip()
        if row["state"] != "done" or any(c.isspace() for c in url):
            continue
        try:
            parsed = urlparse(url)
            if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
                links.append(url)
        except ValueError:
            continue
    return {"name": collection["name"], "links": links, "skipped": len(rows) - len(links)}


async def collection_export_handler(request: web.Request) -> web.Response:
    tag = "tag_id" in request.match_info
    result = await asyncio.to_thread(local_collection_export, request["user"]["id"],
                                     request.match_info["tag_id" if tag else "playlist_id"], tag=tag)
    return web.json_response(result)


def playlist_download_files(user_id: str, playlist_id: str) -> dict[str, Any]:
    with local_db() as db:
        playlist = db.execute("select name from playlists where id=? and user_id=?",
                              (playlist_id, user_id)).fetchone()
        if not playlist:
            raise PublicError("Playlist introuvable.", status=404)
        ids = [row["job_id"] for row in db.execute(
            "select job_id from playlist_items where playlist_id=? order by created_at, rowid", (playlist_id,))]
        order = db.execute("select track_ids from library_orders where user_id=? and scope=?",
                           (user_id, f"playlist:{playlist_id}")).fetchone()
        if order:
            ranks = {job: i for i, job in enumerate(json.loads(order["track_ids"]))}
            ids.sort(key=lambda job: ranks.get(job, len(ranks)))
    files = []
    for number, job_id in enumerate(ids, 1):
        delivery = delivery_for_job(job_id, user_id)
        if delivery:
            member = single_zip_audio(delivery["path"]) if delivery["path"].suffix.lower() == ".zip" else None
            files.append({**delivery, "member": member, "number": number})
    if not files:
        raise PublicError("Aucun fichier disponible dans cette playlist.")
    size = sum(item["member"].file_size if item["member"] else item["size"] for item in files)
    if size > 4 * 1024 ** 3:
        raise PublicError("La playlist depasse 4 Go. Telechargez-la en plusieurs playlists.")
    return {"name": playlist["name"], "files": files, "skipped": len(ids) - len(files), "size": size}


async def playlist_download_ticket_handler(request: web.Request) -> web.Response:
    if request.app[PLAYLIST_ZIP_LOCK_KEY].locked():
        raise PublicError("Une archive est deja en cours de telechargement. Reessayez apres sa fin.", status=429)
    user_id, playlist_id = request["user"]["id"], request.match_info["playlist_id"]
    result = await asyncio.to_thread(playlist_download_files, user_id, playlist_id)
    tickets = request.app[PLAYLIST_TICKETS_KEY]
    now = time.monotonic()
    for key, value in list(tickets.items()):
        if value["expires"] <= now:
            del tickets[key]
    if len(tickets) >= 100:
        raise PublicError("Trop de demandes de telechargement. Reessayez dans quelques minutes.", status=429)
    token = secrets.token_urlsafe(32)
    tickets[hashlib.sha256(token.encode()).hexdigest()] = {
        "user_id": user_id, "playlist_id": playlist_id, "expires": now + DOWNLOAD_TICKET_SECONDS}
    return web.json_response({"url": f"/api/playlist-files/{token}", "count": len(result["files"]),
                              "skipped": result["skipped"]})


def write_playlist_zip(target, files):
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for item in files:
            member = item["member"]
            filename = Path((member.filename if member else item["name"]).replace("\\", "/")).name
            filename = re.sub(r'[\x00-\x1f\x7f]', "_", filename)
            name = f'{item["number"]:03d} - {filename}'
            if member:
                with zipfile.ZipFile(item["path"]) as source, source.open(member) as data:
                    with archive.open(name, "w", force_zip64=True) as output:
                        shutil.copyfileobj(data, output, length=1024 * 1024)
            else:
                archive.write(item["path"], name)
    target.seek(0)


async def playlist_files_handler(request: web.Request) -> web.StreamResponse:
    token = request.match_info["token"]
    ticket = request.app[PLAYLIST_TICKETS_KEY].get(hashlib.sha256(token.encode()).hexdigest())
    if not ticket or ticket["expires"] <= time.monotonic():
        raise PublicError("Lien expire. Relancez le telechargement depuis la playlist.", status=403)
    lock = request.app[PLAYLIST_ZIP_LOCK_KEY]
    if lock.locked():
        raise PublicError("Une archive est deja en cours de telechargement. Reessayez apres sa fin.", status=429)
    async with lock:
        result = await asyncio.to_thread(playlist_download_files, ticket["user_id"], ticket["playlist_id"])
        if shutil.disk_usage(tempfile.gettempdir()).free < result["size"] + 512 * 1024 ** 2:
            raise PublicError("Espace temporaire insuffisant pour preparer cette playlist.", status=507)
        with tempfile.TemporaryFile() as archive:
            task = asyncio.create_task(asyncio.to_thread(write_playlist_zip, archive, result["files"]))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            except (OSError, zipfile.BadZipFile) as exc:
                raise PublicError("Un fichier est devenu indisponible. Relancez le telechargement.") from exc
            size = archive.seek(0, os.SEEK_END)
            archive.seek(0)
            filename = re.sub(r'[/\\\x00-\x1f\x7f]', "_", result["name"])[:100] + ".zip"
            response = web.StreamResponse(headers={
                "Content-Type": "application/zip", "Content-Length": str(size),
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
                "Cache-Control": "no-store",
            })
            _security_headers(response, request)
            await response.prepare(request)
            while chunk := await asyncio.to_thread(archive.read, 1024 * 1024):
                await response.write(chunk)
            await response.write_eof()
            return response


async def playlist_folder_handler(request: web.Request) -> web.Response:
    user_id = request["user"]["id"]
    folder_id = request.match_info.get("folder_id")
    payload = await read_json_object(request) if request.method != "DELETE" else {}
    with local_db() as db:
        if request.method == "POST":
            name = payload.get("name")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or "\x00" in name:
                raise PublicError("Nom de dossier invalide.")
            folder_id = str(uuid.uuid4())
            db.execute("insert into playlist_folders (id, user_id, name) values (?, ?, ?)",
                       (folder_id, user_id, name.strip()))
        else:
            if not db.execute("select 1 from playlist_folders where id = ? and user_id = ?",
                              (folder_id, user_id)).fetchone():
                raise PublicError("Dossier introuvable.", status=404)
            if request.method == "DELETE":
                db.execute("delete from playlist_folders where id = ?", (folder_id,))
            elif "collapsed" in payload:
                if not isinstance(payload["collapsed"], bool):
                    raise PublicError("Etat du dossier invalide.")
                db.execute("update playlist_folders set collapsed = ? where id = ?", (payload["collapsed"], folder_id))
            else:
                name = payload.get("name")
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or "\x00" in name:
                    raise PublicError("Nom de dossier invalide.")
                db.execute("update playlist_folders set name = ? where id = ?", (name.strip(), folder_id))
    return web.json_response({"id": folder_id}, status=201 if request.method == "POST" else 200)


async def playlist_move_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    folder_id = payload.get("folder_id")
    if folder_id is not None and not isinstance(folder_id, str):
        raise PublicError("Dossier invalide.")
    user_id, playlist_id = request["user"]["id"], request.match_info["playlist_id"]
    with local_db() as db:
        if not local_playlist_has_owner(db, playlist_id, user_id):
            raise PublicError("Playlist introuvable.", status=404)
        if folder_id is not None and not db.execute(
            "select 1 from playlist_folders where id = ? and user_id = ?", (folder_id, user_id)
        ).fetchone():
            raise PublicError("Dossier introuvable.", status=404)
        db.execute("delete from playlist_folder_items where playlist_id = ?", (playlist_id,))
        if folder_id is not None:
            db.execute("insert into playlist_folder_items values (?, ?)", (playlist_id, folder_id))
    return web.json_response({"moved": True})


async def cover_handler(request: web.Request) -> web.StreamResponse:
    job_id = request.match_info.get("job_id", "")
    if not musicdl.JOB_ID_RE.fullmatch(job_id):
        raise PublicError("Pochette introuvable.", status=404)
    cover = await asyncio.to_thread(cover_for_job, job_id, request["user"]["id"])
    if not cover:
        raise PublicError("Pochette introuvable.", status=404)
    if "data" in cover:
        return web.Response(
            body=cover["data"],
            headers={
                "Content-Type": cover["mime_type"],
                "Cache-Control": "private, max-age=3600",
            },
        )
    response = web.FileResponse(cover["path"], headers={"Content-Type": cover["mime_type"]})
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


async def playlist_create_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    name = payload.get("name")
    if not isinstance(name, str):
        raise PublicError("Nom de playlist invalide.")
    playlist = await asyncio.to_thread(
        local_create_playlist,
        request["user"]["id"],
        name,
    )
    return web.json_response({"playlist": playlist}, status=201)


async def collection_rename_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    collection = "playlists" if "playlist_id" in request.match_info else "tags"
    item_id = request.match_info.get("playlist_id") or request.match_info.get("tag_id", "")
    name = await asyncio.to_thread(local_rename_collection, request["user"]["id"],
                                   collection, item_id, payload.get("name"))
    return web.json_response({"name": name})


async def playlist_delete_handler(request: web.Request) -> web.Response:
    playlist_id = request.match_info.get("playlist_id", "")
    try:
        playlist_id = str(uuid.UUID(playlist_id))
    except ValueError as exc:
        raise PublicError("Playlist introuvable.", status=404) from exc
    deleted = await asyncio.to_thread(
        local_delete_playlist,
        request["user"]["id"],
        playlist_id,
    )
    if not deleted:
        raise PublicError("Playlist introuvable.", status=404)
    return web.json_response({"deleted": True})


async def tag_create_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    name = payload.get("name")
    if not isinstance(name, str):
        raise PublicError("Nom de tag invalide.")
    tag = await asyncio.to_thread(
        local_create_tag,
        request["user"]["id"],
        name,
        payload.get("color"),
    )
    return web.json_response({"tag": tag}, status=201)


async def tag_delete_handler(request: web.Request) -> web.Response:
    tag_id = request.match_info.get("tag_id", "")
    try:
        tag_id = str(uuid.UUID(tag_id))
    except ValueError as exc:
        raise PublicError("Tag introuvable.", status=404) from exc
    deleted = await asyncio.to_thread(
        local_delete_tag,
        request["user"]["id"],
        tag_id,
    )
    if not deleted:
        raise PublicError("Tag introuvable.", status=404)
    return web.json_response({"deleted": True})


async def playlist_track_add_handler(request: web.Request) -> web.Response:
    playlist_id = request.match_info.get("playlist_id", "")
    try:
        playlist_id = str(uuid.UUID(playlist_id))
    except ValueError as exc:
        raise PublicError("Playlist introuvable.", status=404) from exc
    payload = await read_json_object(request)
    job_id = payload.get("job_id")
    if not isinstance(job_id, str):
        raise PublicError("Morceau invalide.", status=404)
    await asyncio.to_thread(
        local_playlist_add_track,
        request["user"]["id"],
        playlist_id,
        job_id,
    )
    return web.json_response({"added": True})


async def tag_track_add_handler(request: web.Request) -> web.Response:
    tag_id = request.match_info.get("tag_id", "")
    try:
        tag_id = str(uuid.UUID(tag_id))
    except ValueError as exc:
        raise PublicError("Tag introuvable.", status=404) from exc
    payload = await read_json_object(request)
    job_id = payload.get("job_id")
    if not isinstance(job_id, str):
        raise PublicError("Morceau invalide.", status=404)
    await asyncio.to_thread(
        local_tag_add_track,
        request["user"]["id"],
        tag_id,
        job_id,
    )
    return web.json_response({"added": True})


async def playlist_track_delete_handler(request: web.Request) -> web.Response:
    playlist_id = request.match_info.get("playlist_id", "")
    job_id = request.match_info.get("job_id", "")
    try:
        playlist_id = str(uuid.UUID(playlist_id))
    except ValueError as exc:
        raise PublicError("Playlist introuvable.", status=404) from exc
    await asyncio.to_thread(
        local_playlist_remove_track,
        request["user"]["id"],
        playlist_id,
        job_id,
    )
    return web.json_response({"removed": True})


async def tag_track_delete_handler(request: web.Request) -> web.Response:
    tag_id = request.match_info.get("tag_id", "")
    job_id = request.match_info.get("job_id", "")
    try:
        tag_id = str(uuid.UUID(tag_id))
    except ValueError as exc:
        raise PublicError("Tag introuvable.", status=404) from exc
    await asyncio.to_thread(
        local_tag_remove_track,
        request["user"]["id"],
        tag_id,
        job_id,
    )
    return web.json_response({"removed": True})


def normalize_admin_daily_stats(rows: Any, *, days: int = 30) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise SourceError("Réponse statistiques invalide.")
    today = date.today()
    first_day = today - timedelta(days=days - 1)
    totals = {
        (first_day + timedelta(days=offset)).isoformat(): {
            "date": (first_day + timedelta(days=offset)).isoformat(),
            "tracks": 0,
            "savings_cents": 0,
        }
        for offset in range(days)
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("state") not in {"", None, "done"}:
            continue
        created_at = str(row.get("created_at", ""))[:10]
        try:
            day = date.fromisoformat(created_at)
        except ValueError:
            continue
        if day < first_day or day > today:
            continue
        bucket = totals[day.isoformat()]
        try:
            track_count = int(row.get("track_count") or 0)
        except (TypeError, ValueError):
            track_count = 0
        try:
            savings_cents = int(row.get("estimated_savings_cents") or 0)
        except (TypeError, ValueError):
            savings_cents = 0
        bucket["tracks"] += max(track_count, 1)
        bucket["savings_cents"] += max(savings_cents, 0)
    return list(totals.values())


async def admin_profiles_handler(_request: web.Request) -> web.Response:
    profiles = await asyncio.to_thread(local_admin_profiles)
    return web.json_response({"profiles": profiles})


async def admin_profile_handler(request: web.Request) -> web.Response:
    user_id = request.match_info.get("user_id", "")
    try:
        user_id = str(uuid.UUID(user_id))
    except ValueError as exc:
        raise PublicError("Utilisateur invalide.", status=404) from exc
    payload = await read_json_object(request)
    approved = payload.get("approved")
    if not isinstance(approved, bool):
        raise PublicError("Statut invalide.")
    profile = await asyncio.to_thread(local_set_user_access, user_id, approved)
    if not profile:
        raise PublicError("Utilisateur introuvable.", status=404)
    request.app[AUTH_CACHE_KEY].clear()
    return web.json_response({"profile": profile})


async def admin_profile_password_handler(request: web.Request) -> web.Response:
    user_id = request.match_info.get("user_id", "")
    try:
        user_id = str(uuid.UUID(user_id))
    except ValueError as exc:
        raise PublicError("Utilisateur invalide.", status=404) from exc
    payload = await read_json_object(request)
    password = payload.get("password")
    if not isinstance(password, str) or len(password) < 6 or len(password) > 256:
        raise PublicError("Le mot de passe doit faire au moins 6 caractères.")
    updated = await asyncio.to_thread(local_update_user_password, user_id, password)
    if not updated:
        raise PublicError("Utilisateur introuvable.", status=404)
    request.app[AUTH_CACHE_KEY].clear()
    return web.json_response({"updated": True})


async def admin_profile_delete_handler(request: web.Request) -> web.Response:
    user_id = request.match_info.get("user_id", "")
    try:
        user_id = str(uuid.UUID(user_id))
    except ValueError as exc:
        raise PublicError("Utilisateur invalide.", status=404) from exc
    deleted = await asyncio.to_thread(local_delete_user, user_id)
    if not deleted:
        raise PublicError("Utilisateur introuvable.", status=404)
    request.app[AUTH_CACHE_KEY].clear()
    return web.json_response({"deleted": True})


async def admin_history_handler(request: web.Request) -> web.Response:
    user_id = request.query.get("user_id", "")
    params = {
        "select": "*",
        "order": "created_at.desc",
        "limit": "500",
    }
    if user_id:
        try:
            user_id = str(uuid.UUID(user_id))
        except ValueError as exc:
            raise PublicError("Utilisateur invalide.", status=404) from exc
        params["user_id"] = f"eq.{user_id}"
    rows = await asyncio.to_thread(local_admin_history, user_id)
    return web.json_response({"history": rows})


async def admin_stats_handler(_request: web.Request) -> web.Response:
    rows = await asyncio.to_thread(local_daily_history_rows)
    return web.json_response({"daily": normalize_admin_daily_stats(rows)})


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


def update_existing_job(path: Path, job: dict[str, Any]) -> bool:
    with musicdl.provider_lock(job["provider"], "enqueue"):
        if not path.is_file():
            return False
        musicdl.atomic_write_json(path, job)
        return True


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
    if not update_existing_job(path, job):
        raise PublicError("Morceau supprimé.", status=404)
    return web.json_response({"url": f"/api/files/{job_id}?ticket={quote(token)}"})


async def stream_ticket_handler(request: web.Request) -> web.Response:
    payload = await read_json_object(request)
    job_id = payload.get("job_id")
    if not isinstance(job_id, str):
        raise PublicError("Morceau invalide.")
    _path, job = find_done_job(job_id)
    if job.get("owner_id") != request["user"]["id"]:
        raise PublicError("Morceau introuvable.", status=404)
    delivery = delivery_for_job(job_id, request["user"]["id"])
    if not delivery:
        raise PublicError("Morceau introuvable.", status=404)
    if not delivery["mime_type"]:
        raise PublicError("Ce fichier ne peut pas être lu dans le navigateur.")
    token = secrets.token_urlsafe(32)
    # Each deck needs its own URL, valid for later range requests during a set.
    with local_db() as db:
        db.execute("delete from stream_tickets where expires_at <= ?", (time.time(),))
        db.execute("insert into stream_tickets (token_hash, user_id, job_id, expires_at) values (?, ?, ?, ?)",
                   (hashlib.sha256(token.encode()).hexdigest(), request["user"]["id"], job_id,
                    time.time() + STREAM_TICKET_SECONDS))
    return web.json_response({"url": f"/api/stream/{job_id}?ticket={quote(token)}"})


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
    if not update_existing_job(path, job):
        raise PublicError("Morceau supprimé.", status=404)

    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    response = web.StreamResponse(
        headers={
            "Content-Type": content_type,
            "Content-Length": str(candidate.stat().st_size),
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        }
    )
    await response.prepare(request)
    with candidate.open("rb") as handle:
        while chunk := await asyncio.to_thread(handle.read, 1024 * 1024):
            await response.write(chunk)
    await response.write_eof()
    job["delivered_at"] = musicdl.utc_now()
    job["delivery_deleted"] = False
    update_existing_job(path, job)
    return response


async def zip_audio_response(
    request: web.Request,
    path: Path,
    member: zipfile.ZipInfo,
) -> web.StreamResponse:
    content_type = audio_mime_type(Path(member.filename).name)
    if not content_type:
        raise PublicError("Ce fichier ne peut pas être lu dans le navigateur.")
    size = member.file_size
    start, stop, partial = 0, size, False
    if request.headers.get("Range") and not request.headers.get("If-Range"):
        try:
            requested = request.http_range
            start = requested.start or 0
            if start < 0:
                start = max(0, size + start)
            stop = min(size, requested.stop if requested.stop is not None else size)
            if start >= stop:
                raise ValueError("Unsatisfiable range")
        except ValueError:
            raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{size}"})
        partial = True
    headers = {"Content-Type": content_type, "Content-Length": str(stop - start), "Accept-Ranges": "bytes"}
    if partial:
        headers["Content-Range"] = f"bytes {start}-{stop - 1}/{size}"
    response = web.StreamResponse(
        status=206 if partial else 200,
        headers=headers,
    )
    await response.prepare(request)
    if request.method == "HEAD":
        return response
    try:
        with zipfile.ZipFile(path) as archive:
            with archive.open(member) as handle:
                if start:
                    await asyncio.to_thread(handle.seek, start)
                remaining = stop - start
                while remaining and (chunk := await asyncio.to_thread(handle.read, min(remaining, 1024 * 1024))):
                    await response.write(chunk)
                    remaining -= len(chunk)
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise PublicError("Fichier audio illisible.", status=404) from exc
    await response.write_eof()
    return response


async def stream_handler(request: web.Request) -> web.StreamResponse:
    job_id = request.match_info.get("job_id", "")
    ticket = request.query.get("ticket", "")
    _path, job = find_done_job(job_id)
    expected = str(job.get("stream_ticket_hash", ""))
    expires = int(job.get("stream_ticket_expires", 0))
    actual = hashlib.sha256(ticket.encode()).hexdigest() if ticket else ""
    with local_db() as db:
        valid = db.execute("select 1 from stream_tickets where token_hash=? and job_id=? and user_id=? and expires_at>?",
                           (actual, job_id, job.get("owner_id"), time.time())).fetchone()
    # Accept short-lived URLs issued before the streaming upgrade.
    legacy_valid = expected and expires > time.time() and secrets.compare_digest(expected, actual)
    if not valid and not legacy_valid:
        raise PublicError("Lien de lecture invalide ou expiré.", status=403)
    delivery = job.get("delivery")
    if not isinstance(delivery, dict) or not isinstance(delivery.get("path"), str):
        raise PublicError("Morceau introuvable.", status=404)
    candidate = (musicdl.DELIVERY_ROOT / delivery["path"]).resolve()
    root = musicdl.DELIVERY_ROOT.resolve()
    if (
        not candidate.is_relative_to(root)
        or not candidate.is_file()
        or candidate.is_symlink()
    ):
        raise PublicError("Fichier introuvable.", status=404)
    name = str(delivery.get("name") or candidate.name)
    content_type = audio_mime_type(name)
    if not content_type and candidate.suffix.lower() == ".zip":
        member = single_zip_audio(candidate)
        if member:
            return await zip_audio_response(request, candidate, member)
    if not content_type:
        raise PublicError("Ce fichier ne peut pas être lu dans le navigateur.")
    return web.FileResponse(candidate, headers={"Content-Type": content_type})


async def config_handler(_request: web.Request) -> web.Response:
    return web.json_response(
        {
            "public_url": PUBLIC_URL,
        }
    )


async def health_handler(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def index_handler(_request: web.Request) -> web.StreamResponse:
    html = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html")


async def icon_asset_handler(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(ASSET_ROOT / request.path.lstrip("/"))


async def javascript_handler(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(ASSET_ROOT / "app.js", headers={"Content-Type": "text/javascript; charset=utf-8"})


async def stylesheet_handler(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(ASSET_ROOT / "style.css", headers={"Content-Type": "text/css; charset=utf-8"})


def create_app() -> web.Application:
    init_local_store()
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
    app[ADMIN_SESSIONS_KEY] = {}
    app[PLAYLIST_TICKETS_KEY] = {}
    app[PLAYLIST_ZIP_LOCK_KEY] = asyncio.Lock()
    app.cleanup_ctx.append(playlist_import_context)
    app.cleanup_ctx.append(provider_status_context)
    mixer_analysis.register(app, local_db, delivery_for_job, PublicError)
    app.router.add_get("/", index_handler)
    app.router.add_get("/app.js", javascript_handler)
    app.router.add_get("/style.css", stylesheet_handler)
    for asset in ("vendor/mixer-icons.js", "vendor/player-icons.js"):
        app.router.add_get(f"/{asset}", icon_asset_handler)
    app.router.add_get("/api/health", health_handler)
    app.router.add_get("/api/config", config_handler)
    app.router.add_post("/api/signup", signup_handler)
    app.router.add_post("/api/login", login_handler)
    app.router.add_post("/api/email/verify", email_verify_handler)
    app.router.add_post("/api/email/resend", email_resend_handler)
    app.router.add_post("/api/password-reset/request", password_reset_request_handler)
    app.router.add_post("/api/password-reset/confirm", password_reset_confirm_handler)
    app.router.add_post("/api/session/refresh", refresh_session_handler)
    app.router.add_post("/api/logout", logout_handler)
    app.router.add_post("/api/history", history_sync_handler)
    app.router.add_get("/api/history", history_handler)
    app.router.add_get("/api/library", library_handler)
    app.router.add_put("/api/library/order", library_order_handler)
    app.router.add_delete("/api/library/{job_id}", library_track_delete_handler)
    app.router.add_get("/api/covers/{job_id}", cover_handler)
    app.router.add_post("/api/playlists", playlist_create_handler)
    app.router.add_get("/api/playlists/{playlist_id}/export", collection_export_handler)
    app.router.add_post("/api/playlists/{playlist_id}/download-ticket", playlist_download_ticket_handler)
    app.router.add_get("/api/playlist-files/{token}", playlist_files_handler)
    app.router.add_get("/api/tags/{tag_id}/export", collection_export_handler)
    app.router.add_post("/api/playlist-folders", playlist_folder_handler)
    app.router.add_patch("/api/playlist-folders/{folder_id}", playlist_folder_handler)
    app.router.add_delete("/api/playlist-folders/{folder_id}", playlist_folder_handler)
    app.router.add_patch("/api/playlists/{playlist_id}/folder", playlist_move_handler)
    app.router.add_post("/api/playlist-imports", playlist_import_handler)
    app.router.add_delete("/api/playlists/{playlist_id}", playlist_delete_handler)
    app.router.add_patch("/api/playlists/{playlist_id}", collection_rename_handler)
    app.router.add_post("/api/tags", tag_create_handler)
    app.router.add_delete("/api/tags/{tag_id}", tag_delete_handler)
    app.router.add_patch("/api/tags/{tag_id}", collection_rename_handler)
    app.router.add_post("/api/playlists/{playlist_id}/tracks", playlist_track_add_handler)
    app.router.add_delete(
        "/api/playlists/{playlist_id}/tracks/{job_id}",
        playlist_track_delete_handler,
    )
    app.router.add_post("/api/tags/{tag_id}/tracks", tag_track_add_handler)
    app.router.add_delete(
        "/api/tags/{tag_id}/tracks/{job_id}",
        tag_track_delete_handler,
    )
    app.router.add_post("/api/admin/login", admin_login_handler)
    app.router.add_get("/api/admin/profiles", admin_profiles_handler)
    app.router.add_patch("/api/admin/profiles/{user_id}", admin_profile_handler)
    app.router.add_post("/api/admin/profiles/{user_id}/password", admin_profile_password_handler)
    app.router.add_delete("/api/admin/profiles/{user_id}", admin_profile_delete_handler)
    app.router.add_get("/api/admin/history", admin_history_handler)
    app.router.add_get("/api/admin/stats", admin_stats_handler)
    app.router.add_get("/api/status", status_handler)
    app.router.add_post("/api/search", search_handler)
    app.router.add_post("/api/download", download_handler)
    app.router.add_post("/api/downloads", batch_download_handler)
    app.router.add_post("/api/download-ticket", ticket_handler)
    app.router.add_post("/api/stream-ticket", stream_ticket_handler)
    app.router.add_get("/api/files/{job_id}", file_handler)
    app.router.add_get("/api/stream/{job_id}", stream_handler)
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
