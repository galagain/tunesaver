"""Private feedback threads, shared by the SQLite and PostgreSQL deployments."""
import asyncio
import base64
import binascii
import json
import struct
import uuid
from datetime import datetime, timedelta, timezone

from aiohttp import web

STATUSES = {'new', 'in_progress', 'resolved', 'closed'}
SCHEMA = (
    """create table if not exists feedback_threads (
        id text primary key, user_id text not null references users(id) on delete cascade,
        kind text not null, title text not null, status text not null default 'new',
        context text not null, screenshot text not null default '',
        created_at text not null, updated_at text not null,
        revision integer not null default 1, user_read integer not null default 1,
        admin_read integer not null default 0)""",
    """create table if not exists feedback_messages (
        id text primary key, thread_id text not null references feedback_threads(id) on delete cascade,
        author text not null, body text not null, created_at text not null,
        position integer not null, unique(thread_id, position))""",
    'create index if not exists feedback_owner_date on feedback_threads(user_id, updated_at)',
)


def register(app, db_factory, read_json, error):
    with db_factory() as db:
        for statement in SCHEMA:
            db.execute(statement)

    def now():
        return datetime.now(timezone.utc).isoformat()

    def text(value, maximum, label):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
            raise error(f'{label} invalide (1 à {maximum} caractères).')
        return value.strip()

    def screenshot(value):
        if not value:
            return ''
        if not isinstance(value, str) or len(value) > 1400000:
            raise error('Capture trop volumineuse (1 Mo maximum).')
        try:
            raw = base64.b64decode(value, validate=True)
            if len(raw) > 1024 * 1024 or raw[:8] != b'\x89PNG\r\n\x1a\n':
                raise ValueError()
            if raw[12:16] != b'IHDR':
                raise ValueError()
            width, height = struct.unpack('>II', raw[16:24])
            if not (1 <= width <= 2048 and 1 <= height <= 2048):
                raise ValueError()
            offset = 8
            while offset < len(raw):
                length = struct.unpack('>I', raw[offset:offset + 4])[0]
                chunk = raw[offset + 4:offset + 8 + length]
                crc = struct.unpack('>I', raw[offset + 8 + length:offset + 12 + length])[0]
                if binascii.crc32(chunk) != crc:
                    raise ValueError()
                offset += length + 12
            if offset != len(raw) or raw[-8:-4] != b'IEND':
                raise ValueError()
        except (ValueError, binascii.Error, struct.error):
            raise error('Capture PNG invalide.')
        return value

    def get_thread(db, identifier, owner, admin):
        row = db.execute('select * from feedback_threads where id=?', (identifier,)).fetchone()
        if not row or (not admin and row['user_id'] != owner):
            raise error('Retour introuvable.', status=404)
        return row

    def identity(request):
        admin = 'admin' in request
        return ('' if admin else request['user']['id']), admin

    def listing(owner, admin):
        with db_factory() as db:
            rows = db.execute("""select f.id,f.title,f.kind,f.status,f.created_at,f.updated_at,
                f.user_read,f.admin_read,u.email from feedback_threads f
                join users u on u.id=f.user_id """ + ('' if admin else 'where f.user_id=? ') +
                'order by f.updated_at desc,f.id', () if admin else (owner,)).fetchall()
        return {'threads': [{**dict(r), 'unread': not bool(r['admin_read' if admin else 'user_read'])} for r in rows]}

    async def list_handler(request):
        return web.json_response(await asyncio.to_thread(listing, *identity(request)), headers={'Cache-Control': 'no-store'})

    def create(owner, payload):
        kind = payload.get('kind')
        if kind not in {'bug', 'suggestion'}:
            raise error('Choisis un bug ou une suggestion.')
        title = text(payload.get('title'), 160, 'Titre')
        body = text(payload.get('body'), 8000, 'Description')
        image = screenshot(payload.get('screenshot'))
        context = payload.get('context', {})
        if not isinstance(context, dict):
            raise error('Contexte invalide.')
        # Never persist URLs, credentials, local storage, or arbitrary browser diagnostics.
        clean = {k: str(context.get(k, ''))[:60] for k in ('version', 'browser', 'section')}
        stamp, identifier = now(), str(uuid.uuid4())
        with db_factory() as db:
            db.execute('begin immediate')
            cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            count = db.execute('select count(*) from feedback_threads where user_id=? and created_at>=?', (owner, cutoff)).fetchone()[0]
            if count >= 10:
                raise error('Limite de 10 nouveaux retours par jour atteinte.', status=429)
            db.execute('insert into feedback_threads(id,user_id,kind,title,context,screenshot,created_at,updated_at) values(?,?,?,?,?,?,?,?)',
                       (identifier, owner, kind, title, json.dumps(clean), image, stamp, stamp))
            db.execute('insert into feedback_messages(id,thread_id,author,body,created_at,position) values(?,?,?,?,?,?)',
                       (str(uuid.uuid4()), identifier, 'user', body, stamp, 1))
        return {'id': identifier}

    async def create_handler(request):
        payload = await read_json(request)
        return web.json_response(await asyncio.to_thread(create, request['user']['id'], payload), status=201)

    def detail(identifier, owner, admin):
        with db_factory() as db:
            row = get_thread(db, identifier, owner, admin)
            messages = db.execute('select author,body,created_at from feedback_messages where thread_id=? order by position', (identifier,)).fetchall()
            result = {k: row[k] for k in ('id', 'title', 'kind', 'status', 'revision', 'created_at')}
            result.update(context=json.loads(row['context']), has_screenshot=bool(row['screenshot']), messages=[dict(r) for r in messages])
        return result

    async def detail_handler(request):
        result = await asyncio.to_thread(detail, request.match_info['thread_id'], *identity(request))
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    def update(identifier, owner, admin, payload, action):
        stamp = now()
        with db_factory() as db:
            db.execute('begin immediate')
            row = get_thread(db, identifier, owner, admin)
            if action == 'read':
                column = 'admin_read' if admin else 'user_read'
                if payload.get('revision') == row['revision']:
                    db.execute(f'update feedback_threads set {column}=1 where id=?', (identifier,))
                return
            if action == 'status':
                if not admin or payload.get('status') not in STATUSES:
                    raise error('Statut invalide.', status=400)
                if row['status'] == payload['status']:
                    return
                db.execute('update feedback_threads set status=? where id=?', (payload['status'], identifier))
                labels = {'new': 'Nouveau', 'in_progress': 'En cours', 'resolved': 'Résolu', 'closed': 'Classé'}
                body, author = labels[payload['status']], 'status'
            else:
                body, author = text(payload.get('body'), 8000, 'Commentaire'), 'admin' if admin else 'user'
            if row['revision'] >= 500:
                raise error('Cet échange a atteint sa limite de messages.', status=429)
            db.execute('insert into feedback_messages(id,thread_id,author,body,created_at,position) values(?,?,?,?,?,?)',
                       (str(uuid.uuid4()), identifier, author, body, stamp, row['revision'] + 1))
            db.execute('update feedback_threads set updated_at=?,revision=revision+1,user_read=?,admin_read=? where id=?',
                       (stamp, 0 if admin else 1, 1 if admin else 0, identifier))

    async def update_handler(request):
        payload = await read_json(request)
        await asyncio.to_thread(update, request.match_info['thread_id'], *identity(request), payload, request.match_info['action'])
        return web.json_response({'saved': True})

    def image_data(identifier, owner, admin):
        with db_factory() as db:
            row = get_thread(db, identifier, owner, admin)
            if not row['screenshot']:
                raise error('Capture introuvable.', status=404)
            return base64.b64decode(row['screenshot'])

    async def image_handler(request):
        data = await asyncio.to_thread(image_data, request.match_info['thread_id'], *identity(request))
        return web.Response(body=data, content_type='image/png', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    app.router.add_post('/api/feedback', create_handler)
    for prefix in ('/api/feedback', '/api/admin/feedback'):
        app.router.add_get(prefix, list_handler)
        app.router.add_get(prefix + '/{thread_id}', detail_handler)
        app.router.add_get(prefix + '/{thread_id}/screenshot', image_handler)
        app.router.add_post(prefix + '/{thread_id}/{action:read|comment}', update_handler)
    app.router.add_post('/api/admin/feedback/{thread_id}/{action:status}', update_handler)
