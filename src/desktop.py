"""Single-user macOS host. All traffic and files stay on the user's machine.

The Swift shell owns this process through stdin; EOF closes the HTTP listener,
provider workers and temporary credentials. Credentials persist in Keychain.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import time
from urllib.parse import urlparse

LOCAL_USER_ID = "ebd4511b-ccfc-427e-8b41-83426ec5bc38"
COOKIE = "tunesaver_desktop"
SERVICE = "com.tunesaver.desktop.platforms"


def configure(data: Path, resources: Path) -> None:
    # A shell's old deployment settings must never point the Mac app at a server.
    for name in list(os.environ):
        if name.startswith(("MUSICDL_", "TUNESAVER_", "TIDDL_")):
            os.environ.pop(name)
    settings = {
        "TUNESAVER_DESKTOP": "1",
        "TUNESAVER_ALLOWED_HOSTS": "127.0.0.1",
        "TUNESAVER_DB_PATH": str(data / "library.sqlite"),
        "MUSICDL_QUEUE_DIR": str(data / "queue"),
        "MUSICDL_STATE_DIR": str(data / "session"),
        "MUSICDL_SECRETS_DIR": str(data / "session" / "secrets"),
        "MUSICDL_CONFIG_DIR": str(resources / "config"),
        "MUSICDL_WEB_ASSET_DIR": str(resources / "web"),
        "MUSICDL_DELIVERY_DIR": str(data / "Music"),
        "MUSICDL_BEATPORT_CONFIG": str(resources / "config" / "beatport.json"),
        "MUSICDL_BEATPORT_SEARCH_BINARY": str(resources / "bin" / "beatport-search"),
        "MUSICDL_QOBUZ_SEARCH_SCRIPT": str(resources / "src" / "qobuz_search.py"),
        "MUSICDL_QOBUZ_CONFIG": str(data / "session/qobuz/.config/qobuz-dl/config.ini"),
        "TIDDL_PATH": str(data / "session/tiddl"),
        "PLAYWRIGHT_BROWSERS_PATH": str(resources / "browsers"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join((str(resources / "src"), str(resources))),
        "PATH": os.pathsep.join((str(resources / "bin"), str(Path(sys.executable).parent), "/usr/bin", "/bin")),
    }
    for provider in ("TIDDL", "BEATPORT", "QOBUZ"):
        settings[f"MUSICDL_{provider}_DOWNLOAD_DIR"] = str(data / "Downloads" / provider.lower())
    os.environ.update(settings)
    if str(resources) not in sys.path:
        sys.path.insert(0, str(resources))


class KeychainVault:
    def __init__(self):
        # Explicit backend: never silently fall back to plaintext storage.
        from keyring.backends.macOS import Keyring
        self.backend = Keyring()

    def get(self, provider):
        value = self.backend.get_password(SERVICE, provider)
        return json.loads(value) if value else {}

    def set(self, provider, value):
        self.backend.set_password(SERVICE, provider, json.dumps(value))

    def delete(self, provider):
        if self.backend.get_password(SERVICE, provider) is not None:
            self.backend.delete_password(SERVICE, provider)


class Desktop:
    def __init__(self, data: Path, vault=None):
        import musicdl
        import webapp
        self.api = webapp
        self.music = musicdl
        self.data = data
        self.vault = vault if vault is not None else KeychainVault()
        self.bootstrap = secrets.token_urlsafe(32)
        self.cookie = secrets.token_urlsafe(32)
        self.workers = {}
        self.jobs = {}
        self.tasks = {}
        self.persisted = {}
        self.storage_error = ""
        self.files = {
            "tiddl": [musicdl.STATE_ROOT / "tiddl/auth.json"],
            "beatport": [musicdl.SECRETS_ROOT / "beatport_username",
                         musicdl.SECRETS_ROOT / "beatport_password", webapp.BEATPORT_CACHE_PATH],
            "qobuz": [musicdl.QOBUZ_CONFIG_PATH],
        }

    def snapshot(self, provider):
        return {p.name: p.read_text() for p in self.files[provider] if p.is_file()}

    def persist(self, provider):
        value = self.snapshot(provider)
        if value and value != self.persisted.get(provider):
            self.vault.set(provider, value)
            self.persisted[provider] = value

    def restore(self):
        for provider, paths in self.files.items():
            try:
                value = self.vault.get(provider)
            except Exception:
                self.storage_error = "Le Trousseau est verrouillé ou inaccessible. Tes morceaux restent disponibles. Déverrouille-le et relance TuneSaver pour retrouver tes comptes."
                continue
            for path in paths:
                if path.name in value:
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    path.write_text(value[path.name])
                    path.chmod(0o600)
            self.persisted[provider] = value

    def connected(self, provider):
        try:
            if provider == "tiddl":
                return bool(json.loads(self.files[provider][0].read_text()).get("refresh_token"))
            if provider == "beatport":
                return all(path.is_file() and path.stat().st_size for path in self.files[provider][:2])
            return bool(self.music.read_qobuz_config().defaults().get("user_auth_token"))
        except (OSError, ValueError, RuntimeError):
            return False

    def start_worker(self, provider):
        current = self.workers.get(provider)
        if current is not None and current.poll() is None:
            return
        log = (self.data / "logs" / f"{provider}.log").open("ab")
        try:
            self.workers[provider] = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("musicdl.py")), "worker", provider],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                env={**os.environ, "TUNESAVER_PARENT_PID": str(os.getpid())})
        finally:
            log.close()

    async def stop_worker(self, provider):
        process = self.workers.pop(provider, None)
        if process is None or process.poll() is not None:
            return
        process.terminate()  # musicdl forwards termination to its downloader group.
        try:
            await asyncio.to_thread(process.wait, 8)
        except subprocess.TimeoutExpired:
            process.send_signal(signal.SIGTERM)  # second signal forces the child to stop
            try:
                await asyncio.to_thread(process.wait, 3)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)

    async def maintenance(self):
        while True:
            await asyncio.sleep(15)
            for provider in self.files:
                if self.tasks.get(provider) and not self.tasks[provider].done():
                    continue
                if self.connected(provider):
                    try:
                        with self.music.provider_lock(provider, "operation", blocking=False):
                            await asyncio.to_thread(self.persist, provider)
                        self.storage_error = ""
                    except RuntimeError:
                        continue
                    except Exception:
                        self.storage_error = "Le Trousseau macOS est inaccessible. Déverrouille-le pour mémoriser tes connexions."
                    self.start_worker(provider)

    async def context(self, app):
        await asyncio.to_thread(self.restore)
        for provider in self.files:
            if self.connected(provider):
                self.start_worker(provider)
        monitor = asyncio.create_task(self.maintenance())
        try:
            yield
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            # Do not cancel to_thread credential writes midway through a Keychain update.
            for provider, task in self.tasks.items():
                if provider == "tiddl":
                    task.cancel()
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            await asyncio.gather(*(self.stop_worker(p) for p in list(self.workers)))
            for provider in self.files:
                try:
                    self.persist(provider)
                except Exception:
                    logging.error("Keychain save failed for %s", provider)

    def register(self, app):
        from aiohttp import web
        with self.api.local_db() as db:
            db.execute("insert or ignore into users (id,email,password_salt,password_hash,email_verified,approved,created_at) values (?,?,?,?,1,1,?)",
                       (LOCAL_USER_ID, "local@tunesaver.invalid", "", "", self.music.utc_now()))
        app.cleanup_ctx.append(self.context)
        app.router.add_get("/desktop/start", self.bootstrap_handler)
        app.router.add_get("/api/desktop/accounts", self.status_handler)
        app.router.add_post("/api/desktop/accounts/{provider}", self.connect_handler)
        app.router.add_delete("/api/desktop/accounts/{provider}", self.disconnect_handler)
        for name in ("desktop.js", "desktop.css"):
            async def asset(request, filename=name):
                return web.FileResponse(self.api.ASSET_ROOT / filename)
            app.router.add_get("/" + name, asset)

    async def handle_request(self, request, handler):
        from aiohttp import web
        if request.path != "/desktop/start":
            if not secrets.compare_digest(request.cookies.get(COOKIE, ""), self.cookie):
                raise self.api.PublicError("Session locale requise. Ouvre TuneSaver depuis l’application.", status=401)
        self.api._check_same_origin(request)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.content_type != "application/json":
            raise self.api.PublicError("Le corps doit être en JSON.", status=415)
        if request.path.startswith(("/api/admin", "/api/feedback", "/api/email", "/api/password-reset", "/api/session")) or request.path in {"/api/signup", "/api/login", "/api/logout"}:
            raise self.api.PublicError("Cette fonction serveur n’existe pas dans la version Mac.", status=404)
        request["user"] = {"id": LOCAL_USER_ID, "email": "Sur ce Mac", "approved": True, "email_verified": True}
        response = await handler(request)
        self.api._security_headers(response, request)
        return response

    async def bootstrap_handler(self, request):
        from aiohttp import web
        if not self.bootstrap or not secrets.compare_digest(request.query.get("token", ""), self.bootstrap):
            raise self.api.PublicError("Lien de démarrage expiré.", status=403)
        self.bootstrap = ""
        response = web.HTTPFound("/")
        response.set_cookie(COOKIE, self.cookie, httponly=True, samesite="Strict", path="/")
        response.headers["Cache-Control"] = "no-store"
        return response

    async def status_handler(self, request):
        from aiohttp import web
        return web.json_response({"accounts": {p: {
            "connected": self.connected(p),
            "worker": p in self.workers and self.workers[p].poll() is None,
            **self.jobs.get(p, {}),
        } for p in self.files}, "music_directory": str(self.music.DELIVERY_ROOT),
            "storage_error": self.storage_error,
            "qobuz_notice": "Le connecteur Qobuz actuel permet la connexion et la recherche, mais son téléchargement est affecté par un changement de l’API Qobuz."})

    def provider(self, request):
        provider = request.match_info["provider"]
        if provider not in self.files:
            raise self.api.PublicError("Plateforme inconnue.", status=404)
        return provider

    async def connect_handler(self, request):
        from aiohttp import web
        provider = self.provider(request)
        if provider in self.tasks and not self.tasks[provider].done():
            raise self.api.PublicError("Une connexion est déjà en cours.", status=409)
        if self.connected(provider):
            raise self.api.PublicError("Déconnecte d’abord le compte actuel.", status=409)
        payload = await self.api.read_json_object(request)
        if provider != "tiddl":
            fields = ("username", "password") if provider == "beatport" else ("user_id", "token")
            if any(not isinstance(payload.get(k), str) or not 1 <= len(payload[k]) <= 8192 for k in fields):
                raise self.api.PublicError("Renseigne les informations de connexion.")
            if provider == "qobuz" and not payload["user_id"].isdigit():
                raise self.api.PublicError("L’identifiant Qobuz doit être un nombre.")
        self.jobs[provider] = {"state": "pending", "message": "Connexion en cours…"}
        self.tasks[provider] = asyncio.create_task(self.connect(provider, payload))
        return web.json_response({"started": True}, status=202)

    async def connect(self, provider, payload):
        try:
            # Prevent searches / workers from using credentials before validation.
            with self.music.provider_lock(provider, "operation", blocking=False):
                if provider == "tiddl":
                    await self.tidal_login()
                else:
                    await asyncio.to_thread(self.password_login, provider, payload)
                # Keychain writes cannot be cancelled halfway through a disconnect.
                saving = asyncio.create_task(asyncio.to_thread(self.persist, provider))
                try:
                    await asyncio.shield(saving)
                except asyncio.CancelledError:
                    await saving
                    raise
            self.jobs[provider] = {"state": "connected", "message": "Compte connecté sur ce Mac."}
            self.start_worker(provider)
        except asyncio.CancelledError:
            raise
        except Exception:
            for path in self.files[provider]:
                path.unlink(missing_ok=True)
            self.jobs[provider] = {"state": "error", "message": "Connexion impossible. Vérifie ton compte, ton abonnement, le réseau et l’accès au Trousseau, puis réessaie."}
        finally:
            payload.clear()

    def password_login(self, provider, payload):
        if provider == "beatport":
            for key in ("username", "password"):
                path = self.music.SECRETS_ROOT / ("beatport_" + key)
                path.write_text(payload[key])
                path.chmod(0o600)
            # Auth-only mode validates the account without searching or downloading.
            result = subprocess.run([self.api.BEATPORT_SEARCH_BINARY, "--auth-only",
                "--username-file", str(self.files[provider][0]), "--password-file", str(self.files[provider][1]),
                "--cache-file", str(self.files[provider][2])], capture_output=True, timeout=60,
                env=self.music.runtime_environment())
            if result.returncode:
                raise ValueError("Beatport authentication failed")
        else:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("desktop_qobuz.py"))],
                input=json.dumps(payload).encode(), capture_output=True, timeout=90,
                env=self.music.runtime_environment("qobuz"))
            if result.returncode:
                raise ValueError("Qobuz authentication failed")

    async def tidal_login(self):
        import requests
        from tiddl.core.auth.client import AUTH_URL, get_auth_credentials
        client_id, client_secret = get_auth_credentials()
        response = await asyncio.to_thread(requests.post, f"{AUTH_URL}/device_authorization",
            data={"client_id": client_id, "scope": "r_usr+w_usr+w_sub"}, timeout=(5, 20))
        response.raise_for_status()
        device = response.json()
        url = device["verificationUriComplete"]
        url = url if url.startswith("https://") else "https://" + url
        if urlparse(url).hostname not in {"link.tidal.com", "login.tidal.com", "tidal.com"}:
            raise ValueError("Unexpected authorization host")
        self.jobs["tiddl"] = {"state": "pending", "message": "Autorise TuneSaver dans ton navigateur.",
                              "authorization_url": url, "code": device.get("userCode", "")}
        deadline = time.monotonic() + min(int(device["expiresIn"]), 900)
        interval = max(int(device.get("interval", 5)), 2)
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            response = await asyncio.to_thread(requests.post, f"{AUTH_URL}/token",
                data={"client_id": client_id, "device_code": device["deviceCode"],
                      "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "scope": "r_usr+w_usr+w_sub"},
                auth=(client_id, client_secret), timeout=(5, 20))
            auth = response.json()
            if auth.get("error") == "authorization_pending":
                continue
            if auth.get("error") == "slow_down":
                interval += 5
                continue
            response.raise_for_status()
            self.music.atomic_write_json(self.files["tiddl"][0], {"token": auth["access_token"],
                "refresh_token": auth["refresh_token"], "expires_at": int(time.time()) + int(auth["expires_in"]),
                "user_id": str(auth["user_id"]), "country_code": auth["user"]["countryCode"]})
            return
        raise TimeoutError("Authorization expired")

    async def disconnect_handler(self, request):
        from aiohttp import web
        provider = self.provider(request)
        task = self.tasks.get(provider)
        if task and not task.done():
            if provider != "tiddl":
                raise self.api.PublicError("Attends la fin de la connexion avant de déconnecter le compte.", status=409)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.stop_worker(provider)
        try:
            with self.music.provider_lock(provider, "operation", blocking=False):
                await asyncio.to_thread(self.vault.delete, provider)
                for path in self.files[provider]:
                    path.unlink(missing_ok=True)
        except Exception:
            raise self.api.PublicError("Compte occupé ou Trousseau verrouillé. Réessaie dans un instant.", status=409)
        self.persisted.pop(provider, None)
        self.jobs.pop(provider, None)
        return web.json_response({"disconnected": True})


async def serve(data, parent_watch):
    from aiohttp import web
    import webapp
    desktop = Desktop(data)
    app = webapp.create_app(desktop)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=10)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    if parent_watch:
        def parent_input():
            if not os.read(sys.stdin.fileno(), 1024):
                loop.remove_reader(sys.stdin.fileno())
                stop.set()
        loop.add_reader(sys.stdin.fileno(), parent_input)
    try:
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        print(f"TUNESAVER_READY http://127.0.0.1:{port}/desktop/start?token={desktop.bootstrap}", flush=True)
        await stop.wait()
    finally:
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Library/Application Support/TuneSaver")
    parser.add_argument("--resources", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--parent-watch", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    data = args.data_dir.expanduser().resolve()
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    data.chmod(0o700)
    lock = (data / ".desktop.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("TuneSaver est déjà ouvert pour cette bibliothèque.")
    configure(data, args.resources.resolve())
    # Remove only transient platform sessions left by an interrupted previous run.
    shutil.rmtree(data / "session", ignore_errors=True)
    (data / "session/secrets").mkdir(parents=True, mode=0o700)
    (data / "logs").mkdir(mode=0o700, exist_ok=True)
    import musicdl
    musicdl.ensure_layout()
    musicdl.sync_tiddl_config(musicdl.TIDDL_DOWNLOAD_DIR)
    try:
        asyncio.run(serve(data, args.parent_watch))
    finally:
        shutil.rmtree(data / "session", ignore_errors=True)
        lock.close()


if __name__ == "__main__":
    main()
