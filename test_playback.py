#!/usr/bin/env python3
"""Smoke-test one existing audio stream per provider; no new downloads."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def test_provider(app, provider, base_url, max_start):
    with app.local_db() as db:
        rows = db.execute(
            'select job_id,user_id,title from download_history where provider=? and state=? order by created_at desc',
            (provider, 'done')).fetchall()
    row = next((row for row in rows if (delivery := app.delivery_for_job(row['job_id'], row['user_id']))
                and delivery['mime_type']), None)
    if row is None:
        return 'ABSENT', 'aucun fichier audio disponible'
    ticket_hash = None
    try:
        start = time.monotonic()
        link = app.create_stream_ticket(row['job_id'], row['user_id'])
        token = parse_qs(urlsplit(link).query)['ticket'][0]
        ticket_hash = hashlib.sha256(token.encode()).hexdigest()
        ticket_time = time.monotonic() - start
        url = base_url.rstrip('/') + link
        start = time.monotonic()
        request = Request(url, headers={'Range': 'bytes=0-65535'})
        with urlopen(request, timeout=15) as response:
            if response.status != 206 or not response.headers.get('Content-Range', '').startswith('bytes 0-'):
                return 'ECHEC', 'requetes partielles HTTP non fonctionnelles'
            if not response.headers.get('Content-Type', '').startswith('audio/') or not response.read(1):
                return 'ECHEC', 'flux audio vide ou type incorrect'
            first_byte = time.monotonic() - start
        start = time.monotonic()
        decoded = subprocess.run(
            ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', url,
             '-map', '0:a:0', '-t', '1', '-f', 'null', '-'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
        decode_time = time.monotonic() - start
        if decoded.returncode:
            return 'ECHEC', 'le flux ne peut pas etre decode par FFmpeg'
        label = 'OK' if ticket_time + first_byte <= max_start and decode_time <= max_start else 'LENT'
        title = ' '.join(str(row['title'] or row['job_id']).split())[:100]
        return label, f'{title} | lien {ticket_time:.2f}s | premier octet {first_byte:.2f}s | decodage {decode_time:.2f}s'
    except HTTPError as error:
        return 'ECHEC', f'HTTP {error.code} pendant le test du flux'
    except Exception as error:
        # Exceptions from HTTP/FFmpeg can contain the private ticket URL.
        return 'ECHEC', f'{type(error).__name__} pendant le test du flux'
    finally:
        if ticket_hash:
            with app.local_db() as db:
                db.execute('delete from stream_tickets where token_hash=?', (ticket_hash,))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker', action='store_true', help='executer dans le conteneur web existant')
    parser.add_argument('--container', default='tunesaver-web-1')
    parser.add_argument('--base-url', default='http://127.0.0.1:8080', help='adresse du site vue depuis le conteneur')
    parser.add_argument('--max-start', type=float, default=2, help='seuil en secondes pour signaler LENT')
    args = parser.parse_args()
    if args.max_start <= 0:
        parser.error('--max-start doit etre positif')
    if args.docker:
        return subprocess.run(
            ['docker', 'exec', '-i', args.container, 'python', '-', '--base-url', args.base_url,
             '--max-start', str(args.max_start)], input=Path(__file__).read_text(), text=True).returncode
    sys.path.insert(0, str(Path.cwd() / 'src'))
    sys.path.insert(0, '/app')
    import webapp
    failed = False
    for provider, name in [('tiddl', 'TIDAL'), ('beatport', 'Beatport'), ('qobuz', 'Qobuz')]:
        try:
            status, detail = test_provider(webapp, provider, args.base_url, args.max_start)
        except Exception as error:
            status, detail = 'ECHEC', f'{type(error).__name__} pendant la selection du fichier'
        print(f'{name:8} {status:6} {detail}', flush=True)
        failed |= status != 'OK'
    print('Test serveur uniquement : le clic et la lecture dans Firefox ne sont pas verifies ici.')
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
