"""Short-lived, single-use device grants. No platform secret is stored in this table."""
import json
import secrets
import time
from aiohttp import web
import account_transfer

TTL = 600


def initialize(api):
    if api.database.enabled(): return
    with api.local_db() as db:
        db.execute('create table if not exists device_logins (token_hash text primary key, code text unique not null, expires double precision not null, user_id text, providers text, attempts integer not null default 0)')


def register(app, api):
    initialize(api)

    async def start(request):
        await api.read_json_object(request)
        with api.local_db() as db:
            db.execute('delete from device_logins where expires < ?', (time.time(),))
            if db.execute('select count(*) as n from device_logins').fetchone()['n'] >= 200:
                raise api.PublicError('Trop de connexions en cours. Réessaie plus tard.',status=429)
            secret = secrets.token_urlsafe(32)
            alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
            code = ''.join(secrets.choice(alphabet) for _ in range(8))
            code = code[:4]+'-'+code[4:]
            db.execute('insert into device_logins (token_hash,code,expires) values (?,?,?)',(api.token_hash(secret),code,time.time()+TTL))
        return web.json_response({'device_token':secret,'code':code,'verification_uri':'https://tunesaver.galagain.com/auth/?device='+code,'expires_in':TTL,'interval':3})

    async def approve(request):
        payload = await api.read_json_object(request)
        code,providers=payload.get('code'),payload.get('providers')
        if not isinstance(code,str) or len(code)!=9 or not isinstance(providers,list) or not providers or any(not isinstance(p,str) or p not in account_transfer.NAMES for p in providers):
            raise api.PublicError('Code ou plateformes invalides.')
        token=request.headers.get('Authorization','')[7:].strip()
        user=api.local_user_for_access_token(token)
        if not user or not user['approved'] or not user['email_verified'] or user['blocked']:
            raise api.PublicError('Compte non autorisé.',status=403)
        with api.local_db() as db:
            row=db.execute('select * from device_logins where code=?',(code,)).fetchone()
            if not row or row['expires'] < time.time():raise api.PublicError('Ce code a expiré. Relance la connexion.',status=410)
            if row['user_id']:raise api.PublicError('Ce code a déjà été autorisé.',status=409)
            db.execute('update device_logins set user_id=?,providers=? where code=?',(user['id'],json.dumps(providers),code))
        return web.json_response({'approved':True})

    async def poll(request):
        payload=await api.read_json_object(request)
        secret=payload.get('device_token')
        if not isinstance(secret,str) or not 32<=len(secret)<=100:raise api.PublicError('Connexion invalide.',status=400)
        key=api.token_hash(secret)
        with api.local_db() as db:
            db.execute('begin immediate')
            row=db.execute('select * from device_logins where token_hash=?',(key,)).fetchone()
            if not row or row['expires'] < time.time():raise api.PublicError('Connexion expirée ou déjà utilisée.',status=410)
            if row['attempts']>=250:raise api.PublicError('Trop de tentatives.',status=429)
            db.execute('update device_logins set attempts=attempts+1 where token_hash=?',(key,))
            if not row['user_id']:return web.json_response({'pending':True},status=202)
            user=db.execute('select * from users where id=?',(row['user_id'],)).fetchone()
            if not user or not user['approved'] or not user['email_verified'] or user['blocked']:raise api.PublicError('Compte non autorisé.',status=403)
            # Build the file part inside this transaction so two polls cannot claim it.
            # The sync token is issued afterwards (it has its own transaction).
            selected=json.loads(row['providers'])
            bundle=account_transfer.build_bundle(api,dict(user),selected,include_sync=False)
            db.execute('delete from device_logins where token_hash=?',(key,))
        import catalog_sync
        bundle['sync']=catalog_sync.issue(api,user['id'])
        return web.json_response(bundle)

    app.router.add_post('/api/device/start',start)
    app.router.add_post('/api/device/approve',approve)
    app.router.add_post('/api/device/poll',poll)
