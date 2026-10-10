#!/usr/bin/env python3
"""Download one isolated test track per provider using the real running workers."""
import argparse
import math
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


def choose_url(app, provider, supplied):
    if supplied:
        urls = [supplied]
    else:
        with app.local_db() as db:
            urls = [row['source_url'] for row in db.execute(
                'select source_url from download_history where provider=? and state=? order by created_at desc',
                (provider, 'done'))]
    for url in urls:
        try:
            url = app.musicdl.validate_url(provider, url)
            if app.resource_kind(provider, url) == 'track':
                return url
        except (ValueError, TypeError):
            continue
    raise ValueError('Aucun lien de morceau disponible; fournir un lien avec --tidal, --beatport ou --qobuz.')


def run_test(app, provider, url, timeout):
    music = app.musicdl
    if not app.worker_is_active(provider):
        return False, 'ARRETE : worker non demarre'
    owner = str(uuid.uuid4())
    with music.provider_lock(provider, 'enqueue'):
        job = music.enqueue(provider, [url], owner_id=owner, ephemeral=True,
                            metadata={'title': 'Diagnostic plateforme', 'kind': 'track'})
    start = time.monotonic()
    last_state = None
    try:
        while time.monotonic() - start < timeout:
            for state in music.STATES:
                path = music.QUEUE_ROOT / state / provider / (job + '.json')
                try:
                    payload = music.load_job(path, expected_provider=provider)
                except FileNotFoundError:
                    continue
                if state != last_state:
                    print(f'  {provider}: {state}', flush=True)
                    last_state = state
                if state == 'failed':
                    return False, f'ECHEC : telechargeur termine avec le code {payload.get("last_exit_code", "?")}'
                if state == 'done':
                    delivery = app.delivery_for_job(job, owner)
                    if not delivery or not delivery['mime_type'] or delivery['size'] <= 0:
                        return False, 'ECHEC : aucun fichier audio exploitable'
                    decoded = subprocess.run(
                        ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(delivery['path']),
                         '-map', '0:a:0', '-t', '1', '-f', 'null', '-'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
                    if decoded.returncode:
                        return False, 'ECHEC : fichier recu mais audio illisible'
                    return True, f'OK : nouveau fichier de {delivery["size"] / 1_000_000:.1f} Mo, audio valide, {time.monotonic() - start:.1f}s'
                break
            time.sleep(1)
        return False, f'DELAI DEPASSE ({timeout:g}s, dernier etat : {last_state or "inconnu"})'
    finally:
        with music.provider_lock(provider, 'enqueue'):
            running = False
            for state in music.STATES:
                path = music.QUEUE_ROOT / state / provider / (job + '.json')
                if path.exists():
                    payload = music.load_job(path, expected_provider=provider)
                    if payload.get('owner_id') != owner:
                        raise RuntimeError('Proprietaire du test inattendu; nettoyage refuse')
                    running |= state == 'running'
                    path.unlink(missing_ok=True)
            # Active workers clean their own partial files after cancellation.
            if not running or not app.worker_is_active(provider):
                music.remove_job_files(provider, owner, job)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker', action='store_true')
    parser.add_argument('--container', default='tunesaver-web-1')
    parser.add_argument('--timeout', type=float, default=180)
    for name in ('tidal', 'beatport', 'qobuz'):
        parser.add_argument('--' + name, help='lien direct de test; sinon dernier morceau telecharge sur cette plateforme')
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout doit etre positif et fini')
    if args.docker:
        command = ['docker', 'exec', '-i', args.container, 'python', '-', '--timeout', str(args.timeout)]
        for name in ('tidal', 'beatport', 'qobuz'):
            if getattr(args, name):
                command.extend(['--' + name, getattr(args, name)])
        return subprocess.run(command, input=Path(__file__).read_text(), text=True).returncode
    sys.path.insert(0, str(Path.cwd() / 'src'))
    sys.path.insert(0, '/app')
    import webapp
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(130))
    failed = False
    for provider, name in [('tiddl', 'tidal'), ('beatport', 'beatport'), ('qobuz', 'qobuz')]:
        try:
            url = choose_url(webapp, provider, getattr(args, name))
            print(f'{name.upper()} : nouveau telechargement de {url}', flush=True)
            ok, message = run_test(webapp, provider, url, args.timeout)
        except Exception as error:
            ok, message = False, f'ECHEC : {type(error).__name__}'
        print(f'{name.upper()} : {message}', flush=True)
        failed |= not ok
    print('Tests isoles, sans ajout aux comptes. Fichiers termines supprimes; travaux actifs annules.')
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
