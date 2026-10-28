#!/usr/bin/env python3
"""TuneSaver terminal client: download directly, without HTTP or a GUI process."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from tunesaver_version import VERSION

NAMES = {"tiddl": "TIDAL", "beatport": "Beatport", "qobuz": "Qobuz"}
ALIASES = {"tidal": "tiddl", "tiddl": "tiddl", "beatport": "beatport", "qobuz": "qobuz"}
COMMANDS = {"download", "search", "login", "logout", "status", "install"}


def normalize_argv(argv):
    argv = list(argv)
    index = 0
    while index < len(argv):
        if argv[index] in {"--resources", "--data-dir"}:
            index += 2
        elif argv[index].startswith(("--resources=", "--data-dir=")):
            index += 1
        else:
            break
    if index < len(argv) and not argv[index].startswith("-") and argv[index] not in COMMANDS:
        value = argv[index]
        command = "download" if value.startswith(("http://", "https://", "track/", "album/", "playlist/", "mix/", "video/", "artist/")) else "search"
        argv.insert(index, command)
    return argv


def build_parser():
    parser = argparse.ArgumentParser(prog="tunesaver", description="Télécharge ta musique sur ce Mac, directement depuis le terminal.",
        epilog='Exemples : tunesaver "https://tidal.com/track/123" ; tunesaver login tidal ; tunesaver search "artiste titre"')
    parser.add_argument("--version", action="version", version=f"TuneSaver {VERSION}")
    parser.add_argument("--resources", type=Path, default=Path(__file__).resolve().parents[1], help=argparse.SUPPRESS)
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Library/Application Support/TuneSaver", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command")
    download = commands.add_parser("download", help="Télécharger un ou plusieurs liens TIDAL, Beatport ou Qobuz")
    download.add_argument("urls", nargs="+")
    download.add_argument("-o", "--output", type=Path, default=Path.home() / "Music/TuneSaver", help="Dossier de destination (défaut : ~/Music/TuneSaver)")
    search = commands.add_parser("search", help="Rechercher un titre sur les comptes connectés")
    search.add_argument("query", nargs="+")
    search.add_argument("-p", "--provider", choices=ALIASES)
    search.add_argument("--json", action="store_true", help="Afficher les résultats JSON sans demander de sélection")
    search.add_argument("-o", "--output", type=Path, default=Path.home() / "Music/TuneSaver")
    for name, help_text in (("login", "Connecter son propre compte"), ("logout", "Déconnecter un compte de ce Mac")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("provider", choices=ALIASES)
    commands.add_parser("status", help="Afficher les comptes connectés, sans révéler leurs identifiants")
    install = commands.add_parser("install", help="Installer cette commande dans le PATH, sans sudo")
    install.add_argument("--bin-dir", type=Path)
    return parser


def detect_provider(value):
    import musicdl
    for provider in NAMES:
        try:
            return provider, musicdl.validate_url(provider, value)
        except ValueError:
            continue
    raise ValueError("Lien non pris en charge. Utilise un lien HTTPS TIDAL, Beatport ou Qobuz, ou « tunesaver search artiste titre ».")


def make_accounts(data, vault=None):
    from desktop import Desktop
    class TerminalAccounts(Desktop):
        def start_worker(self, provider):
            pass  # A terminal invocation runs just the requested operation.
    return TerminalAccounts(data, vault)


async def login_async(accounts, provider, payload):
    accounts.jobs[provider] = {"state": "pending"}
    task = asyncio.create_task(accounts.connect(provider, payload))
    last_url = None
    try:
        while not task.done():
            url = accounts.jobs.get(provider, {}).get("authorization_url")
            if url and url != last_url:
                print(f"Autorise ton compte TIDAL dans ton navigateur :\n{url}", flush=True)
                last_url = url
            await asyncio.sleep(.2)
        await task
    finally:
        if not task.done():
            if provider == "tiddl":
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    state = accounts.jobs.get(provider, {})
    if state.get("state") != "connected":
        print(state.get("message", "Connexion interrompue."), file=sys.stderr)
        return 1
    print(f"{NAMES[provider]} connecté. Connexion mémorisée dans le Trousseau macOS.")
    return 0


def login(accounts, provider):
    if accounts.connected(provider):
        print(f"{NAMES[provider]} est déjà connecté. Pour changer de compte : tunesaver logout {provider.replace('tiddl', 'tidal')}")
        return 0
    if not sys.stdin.isatty():
        print("La connexion nécessite un terminal interactif. Les mots de passe ne se passent pas en argument.", file=sys.stderr)
        return 2
    payload = {}
    if provider == "beatport":
        payload = {"username": input("Identifiant Beatport : ").strip(), "password": getpass.getpass("Mot de passe : ")}
    elif provider == "qobuz":
        print("Connecte-toi sur https://play.qobuz.com puis copie id et user_auth_token de localuser (stockage local du navigateur).")
        print("Le téléchargement du connecteur Qobuz est actuellement affecté par un changement de son API.")
        payload = {"user_id": input("Identifiant numérique Qobuz : ").strip(), "token": getpass.getpass("Jeton de session : ")}
        if not payload["user_id"].isdigit():
            raise ValueError("L’identifiant Qobuz doit être un nombre.")
    if any(not value or len(value) > 8192 for value in payload.values()):
        raise ValueError("Informations de connexion incomplètes ou trop longues.")
    print(f"Connexion à {NAMES[provider]}…", flush=True)
    return asyncio.run(login_async(accounts, provider, payload))


def logout(accounts, provider):
    # Both interfaces use the same operation lock and the same transient files.
    with accounts.music.provider_lock(provider, "operation", blocking=False):
        accounts.vault.delete(provider)
        for path in accounts.files[provider]:
            path.unlink(missing_ok=True)
        accounts.persisted.pop(provider, None)
    print(f"{NAMES[provider]} déconnecté de ce Mac. Tes fichiers sont conservés.")
    return 0


def download(accounts, values, output):
    # Validate all input before downloading anything.
    groups = {}
    for value in values:
        provider, url = detect_provider(value)
        if url not in groups.setdefault(provider, []):
            groups[provider].append(url)
    output = output.expanduser().resolve()
    failed = False
    for provider, urls in groups.items():
        if not accounts.connected(provider):
            command_name = "tidal" if provider == "tiddl" else provider
            if sys.stdin.isatty():
                if login(accounts, provider):
                    failed = True
                    continue
            else:
                print(f"{NAMES[provider]} n’est pas connecté. Lance : tunesaver login {command_name}", file=sys.stderr)
                failed = True
                continue
        if provider == "qobuz":
            print("Attention : le connecteur Qobuz peut refuser le téléchargement à cause du changement de son API.", file=sys.stderr)
        output.mkdir(parents=True, exist_ok=True)
        log = accounts.music.QUEUE_ROOT / "logs" / provider / f"terminal-{int(time.time())}-{uuid.uuid4().hex[:8]}.log"
        print(f"{NAMES[provider]} → {output}", flush=True)
        try:
            accounts.music.secure_provider_state(provider)
            with accounts.music.provider_lock(provider, "operation", blocking=False):
                # A GUI logout may have completed between the initial check and lock.
                if not accounts.connected(provider):
                    raise RuntimeError("Le compte vient d’être déconnecté. Relance tunesaver login.")
                try:
                    code = accounts.music._run_provider_unlocked(provider, urls, log, output_dir=output)
                finally:
                    accounts.persist(provider)
            if code:
                failed = True
                print(f"Échec {NAMES[provider]}. Diagnostic local : {log}", file=sys.stderr)
            else:
                print(f"Téléchargement terminé : {output}")
        except RuntimeError:
            print(f"{NAMES[provider]} est occupé ou sa connexion est indisponible. Réessaie après l’opération en cours.", file=sys.stderr)
            failed = True
    return 1 if failed else 0


def search(accounts, query, provider=None, as_json=False, output=None):
    import webapp
    providers = [provider] if provider else [p for p in NAMES if accounts.connected(p)]
    if not providers or any(not accounts.connected(p) for p in providers):
        print("Connecte d’abord un compte : tunesaver login tidal (ou beatport, qobuz).", file=sys.stderr)
        return 2
    results = []
    functions = {"tiddl": webapp.search_tidal, "beatport": webapp.search_beatport, "qobuz": webapp.search_qobuz}
    for name in providers:
        try:
            try:
                results.extend(functions[name](webapp.normalized_query(query)))
            finally:
                # Search helpers lock their own token refresh. Save the refreshed
                # credentials before the final terminal session removes them.
                with accounts.music.provider_lock(name, "operation", blocking=False):
                    if accounts.connected(name):
                        accounts.persist(name)
        except (webapp.PublicError, RuntimeError, OSError, ValueError):
            print(f"Recherche {NAMES[name]} indisponible pour le moment.", file=sys.stderr)
    if as_json:
        print(json.dumps(results, ensure_ascii=False))
        return 0 if results else 1
    for index, result in enumerate(results, 1):
        print(f"{index:2}. {result.get('artist', '')} — {result.get('title', '')} [{NAMES.get(result.get('provider'), '')}]")
        print(f"    {result.get('download_url') or result.get('url', '')}")
    if not results:
        print("Aucun résultat.")
        return 1
    if not sys.stdin.isatty():
        return 0
    choice = input("Numéro à télécharger (Entrée pour annuler) : ").strip()
    if not choice:
        return 0
    if not choice.isdigit() or not 1 <= int(choice) <= len(results):
        raise ValueError("Numéro de résultat invalide.")
    result = results[int(choice) - 1]
    return download(accounts, [result.get("download_url") or result["url"]], output or Path.home() / "Music/TuneSaver")


def interactive(accounts):
    if not sys.stdin.isatty():
        values = [line.strip() for line in sys.stdin if line.strip()]
        return download(accounts, values, Path.home() / "Music/TuneSaver") if values else 0
    print("TuneSaver — terminal\nColle un lien TIDAL, Beatport ou Qobuz, ou cherche un artiste et un titre.\nEntrée vide pour quitter. Ctrl+C pour interrompre.")
    while True:
        value = input("tunesaver › ").strip()
        if not value:
            return 0
        if value.startswith(("https://", "http://", "track/", "album/", "playlist/")):
            download(accounts, [value], Path.home() / "Music/TuneSaver")
        else:
            search(accounts, value)


def run(args, *, vault=None):
    from desktop import configure
    from local_session import LocalSession
    os.umask(0o077)
    data = args.data_dir.expanduser().resolve()
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    data.chmod(0o700)
    configure(data, args.resources.resolve())
    (data / "logs").mkdir(mode=0o700, exist_ok=True)
    accounts = make_accounts(data, vault)
    with LocalSession(data, accounts.restore, terminal=True):
        if accounts.storage_error:
            print(accounts.storage_error, file=sys.stderr)
        if args.command == "status":
            for provider, name in NAMES.items():
                print(f"{name}: {'connecté' if accounts.connected(provider) else 'non connecté'}")
            return 0
        if args.command == "login":
            return login(accounts, ALIASES[args.provider])
        if args.command == "logout":
            return logout(accounts, ALIASES[args.provider])
        if args.command == "download":
            return download(accounts, args.urls, args.output)
        if args.command == "search":
            return search(accounts, " ".join(args.query), ALIASES.get(args.provider), args.json, args.output)
        return interactive(accounts)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(normalize_argv(sys.argv[1:] if argv is None else argv))
    if args.command == "install":
        command = [sys.executable, "-B", str(args.resources / "install_cli.py"), "--resources", str(args.resources)]
        if args.bin_dir:
            command.extend(["--bin-dir", str(args.bin_dir)])
        return subprocess.call(command)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        return run(args)
    except (KeyboardInterrupt, EOFError):
        print("\nOpération interrompue.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError) as exc:
        # Only locally constructed validation errors may be shown verbatim.
        message = str(exc) if isinstance(exc, ValueError) or type(exc) is RuntimeError else "Accès aux fichiers ou au Trousseau impossible. Vérifie les permissions et réessaie."
        print(f"tunesaver : {message}", file=sys.stderr)
        return 2
    except Exception:
        print("tunesaver : opération impossible. Vérifie le réseau, ton compte et l’accès au Trousseau macOS.", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
