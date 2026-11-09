"""Metadata-only global catalogue. Audio and local paths never leave the client."""
from contextlib import contextmanager
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlparse

SERVER = 'https://tunesaver.galagain.com'


def clean(row):
    if not isinstance(row, dict) or set(row) - {'title', 'artist', 'url', 'provider'}:
        raise ValueError('Métadonnées invalides')
    result = {}
    for key, limit in (('title', 500), ('artist', 500), ('url', 2048), ('provider', 16)):
        value = row.get(key, '')
        if not isinstance(value, str) or len(value) > limit or '\x00' in value:
            raise ValueError('Métadonnées invalides')
        result[key] = value.strip()
    if not result['title'] or result['provider'] not in ('tiddl', 'beatport', 'qobuz'):
        raise ValueError('Titre ou plateforme absent')
    parsed = urlparse(result['url'])
    domains = {'tiddl':('tidal.com',), 'beatport':('beatport.com',), 'qobuz':('qobuz.com',)}
    if parsed.scheme != 'https' or parsed.username or parsed.password or not any(parsed.hostname == d or (parsed.hostname or '').endswith('.'+d) for d in domains[result['provider']]):
        raise ValueError('Lien de plateforme invalide')
    return result


def fingerprint(row):
    parts = urlparse(row['url']).path.strip('/').split('/')
    # A track ID identifies the same recording even if tags or URL slugs differ.
    if 'track' in parts and parts[-1].isdigit():
        identity = row['provider'] + ':track:' + parts[-1]
    else:
        identity = json.dumps({k:row[k] for k in ('title','artist','url','provider')}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(identity.encode()).hexdigest()


def audio_rows(paths, provider, url):
    from mutagen import File
    rows = []
    for path in paths:
        try:
            tags = File(path, easy=True)
            if tags is None: continue
            title = (tags.get('title') or [path.stem])[0]
            artist = ', '.join(tags.get('artist') or [])
            rows.append(clean({'title':title, 'artist':artist, 'url':url, 'provider':provider}))
        except Exception:
            continue
    return rows


def job_rows(music, job):
    urls = job.get('urls') or []
    if not urls: return []
    url = urls[0]
    if job['provider'] == 'tiddl' and not url.startswith('https://'):
        url = 'https://tidal.com/' + url
    rows = []
    # Only owned delivery directories under the configured audio root.
    if job.get('owner_id') and music.JOB_ID_RE.fullmatch(job.get('id','')):
        root = music.DELIVERY_ROOT.resolve()
        folder = root / str(job['owner_id']) / job['id'] / 'content'
        if folder.resolve().is_relative_to(root) and folder.is_dir() and not folder.is_symlink():
            paths = [p for p in folder.rglob('*') if p.suffix.lower() in music.AUDIO_EXTENSIONS and p.is_file() and not p.is_symlink()]
            rows = audio_rows(paths, job['provider'], url)
    if not rows:
        display = job.get('display') or {}
        if display.get('title') and display.get('kind', 'track') in ('track', 'video'):
            try: rows = [clean({'title':display['title'], 'artist':display.get('artist',''), 'url':url, 'provider':job['provider']})]
            except ValueError: pass
    return rows


def init_server(api):
    if api.database.enabled():
        return  # PostgreSQL schema is installed by migrations/003_catalog.sql.
    with api.local_db() as db:
        db.execute('create table if not exists global_catalog (id text primary key, title text not null, artist text not null, url text not null, provider text not null, added_at text not null)')
        db.execute('create table if not exists catalog_devices (token_hash text primary key, user_id text not null, label text not null, revoked integer not null default 0)')
        db.execute('create table if not exists catalog_scanned (job_id text primary key, stamp text not null)')


def issue(api, user_id='', label='TuneSaver'):
    token = secrets.token_urlsafe(32)
    with api.local_db() as db:
        db.execute('insert into catalog_devices (token_hash,user_id,label) values (?,?,?)', (api.token_hash(token),user_id,label[:80]))
    return {'server':SERVER, 'token':token}


def save_server(api, rows):
    rows = [clean(row) for row in rows]
    with api.local_db() as db:
        for row in rows:
            db.execute('insert into global_catalog (id,title,artist,url,provider,added_at) values (?,?,?,?,?,?) on conflict(id) do nothing', (fingerprint(row),row['title'],row['artist'],row['url'],row['provider'],api.utc_timestamp()))


def harvest_server(api):
    music = api.download_engine
    with api.local_db() as db:
        seen = {r['job_id']:r['stamp'] for r in db.execute('select job_id,stamp from catalog_scanned')}
    for provider in music.PROVIDERS:
        for path in (music.QUEUE_ROOT/'done'/provider).glob('*.json'):
            try:
                stamp = str(path.stat().st_mtime_ns)
                if seen.get(path.stem) == stamp: continue
                job = music.load_job(path, expected_provider=provider)
                save_server(api, job_rows(music, job))
                with api.local_db() as db:
                    db.execute('insert into catalog_scanned (job_id,stamp) values (?,?) on conflict(job_id) do update set stamp=excluded.stamp', (path.stem,stamp))
            except (OSError,ValueError,KeyError): continue


@contextmanager
def local_db(data):
    data = Path(data); data.mkdir(parents=True, exist_ok=True)
    path = data/'catalog-sync.sqlite'
    db = sqlite3.connect(path, timeout=10)
    path.chmod(0o600)
    db.execute('create table if not exists outbox (id text primary key,payload text not null,sent integer not null default 0)')
    db.execute('create table if not exists scanned (id text primary key,stamp text not null)')
    try:
        with db: yield db
    finally: db.close()


def enqueue(data, rows):
    with local_db(data) as db:
        for row in rows:
            row = clean(row)
            db.execute('insert or ignore into outbox (id,payload) values (?,?)', (fingerprint(row),json.dumps(row)))


def harvest_local(data, music):
    with local_db(data) as db:
        seen = dict(db.execute('select id,stamp from scanned'))
    for provider in music.PROVIDERS:
        for path in (music.QUEUE_ROOT/'done'/provider).glob('*.json'):
            try:
                stamp = str(path.stat().st_mtime_ns)
                if seen.get(path.stem) == stamp: continue
                enqueue(data,job_rows(music,music.load_job(path,expected_provider=provider)))
                with local_db(data) as db:
                    db.execute('insert into scanned (id,stamp) values (?,?) on conflict(id) do update set stamp=excluded.stamp',(path.stem,stamp))
            except (OSError,ValueError,KeyError): continue


def valid_config(value):
    if not isinstance(value,dict) or value.get('server') != SERVER or not isinstance(value.get('token'),str) or not 30 <= len(value['token']) <= 200:
        raise ValueError('Configuration de synchronisation invalide')
    return {'server':SERVER,'token':value['token']}


def flush(data, config):
    import requests
    if not config: return {'connected':False}
    config = valid_config(config)
    with local_db(data) as db:
        batch = db.execute('select id,payload from outbox where sent=0 limit 100').fetchall()
    if batch:
        response = requests.post(config['server']+'/api/catalog-sync', json={'tracks':[json.loads(r[1]) for r in batch]}, headers={'Authorization':'Bearer '+config['token']}, timeout=(5,15), allow_redirects=False)
        if response.status_code != 200: raise RuntimeError('Synchronisation indisponible')
        with local_db(data) as db:
            db.executemany('update outbox set sent=1 where id=?',[(r[0],) for r in batch])
    with local_db(data) as db:
        total,pending = db.execute('select count(*),coalesce(sum(1-sent),0) from outbox').fetchone()
    return {'connected':True,'total':total,'pending':pending}


def register(app, api, desktop=False):
    from aiohttp import web
    init_server(api)
    async def ingest(request):
        token = request.headers.get('Authorization','').removeprefix('Bearer ')
        with api.local_db() as db:
            device = db.execute('select * from catalog_devices where token_hash=? and revoked=0',(api.token_hash(token),)).fetchone()
            if not device: raise api.PublicError('Connexion de synchronisation requise.',status=401)
            if device['user_id']:
                user=db.execute('select * from users where id=?',(device['user_id'],)).fetchone()
                if not user or not user['approved'] or not user['email_verified'] or user['blocked']: raise api.PublicError('Compte non autorisé.',status=403)
        payload=await api.read_json_object(request)
        rows=payload.get('tracks')
        if set(payload) != {'tracks'} or not isinstance(rows,list) or len(rows)>100: raise api.PublicError('Lot invalide.')
        try: await asyncio.to_thread(save_server,api,rows)
        except ValueError: raise api.PublicError('Métadonnées invalides.')
        return web.json_response({'accepted':len(rows)})
    async def catalogue(request):
        try: offset=max(0,int(request.query.get('offset','0')))
        except ValueError: raise api.PublicError('Pagination invalide.')
        with api.local_db() as db:
            total=db.execute('select count(*) as n from global_catalog').fetchone()['n']
            rows=[dict(r) for r in db.execute('select title,artist,url,provider from global_catalog order by added_at desc,id limit 100 offset ?',(offset,))]
        return web.json_response({'tracks':rows,'total':total,'offset':offset})
    async def export(request):
        with api.local_db() as db:
            rows=[dict(r) for r in db.execute('select title,artist,url,provider from global_catalog order by added_at,id')]
        return web.json_response(rows,headers={'Content-Disposition':'attachment; filename="catalogue-global.json"'})
    async def context(app):
        async def loop():
            while True:
                try:
                    await asyncio.to_thread(harvest_server,api)
                    config_file = os.environ.get('TUNESAVER_CATALOG_CONFIG')
                    if config_file:
                        data = Path(config_file).parent
                        await asyncio.to_thread(harvest_local,data,api.download_engine)
                        await asyncio.to_thread(flush,data,json.loads(Path(config_file).read_text()))
                except Exception: api.LOGGER.warning('Catalogue global : collecte différée')
                await asyncio.sleep(30)
        task=asyncio.create_task(loop())
        try: yield
        finally:
            task.cancel(); await asyncio.gather(task,return_exceptions=True)
    app.router.add_post('/api/catalog-sync',ingest)
    app.router.add_get('/api/admin/catalog',catalogue)
    app.router.add_get('/api/admin/catalog/export',export)
    if not desktop: app.cleanup_ctx.append(context)
