"""Explicit, authenticated export of platform connections; never log bundle contents."""
import configparser
import io
import json
from contextlib import ExitStack
from aiohttp import web

FORMAT = 'tunesaver-accounts-v1'
MAX_SIZE = 256 * 1024
NAMES = {'tiddl': {'auth.json'}, 'beatport': {'beatport_username', 'beatport_password', 'beatportdl-credentials.json'}, 'qobuz': {'config.ini'}}


def validate(bundle):
    if not isinstance(bundle, dict) or bundle.get('format') != FORMAT:
        raise ValueError('Fichier de comptes TuneSaver invalide.')
    accounts = bundle.get('accounts')
    if not isinstance(accounts, dict) or not accounts or set(accounts) - NAMES.keys():
        raise ValueError('Plateformes invalides.')
    if len(json.dumps(bundle).encode()) > MAX_SIZE:
        raise ValueError('Fichier trop volumineux.')
    cleaned = {}
    for provider, files in accounts.items():
        if not isinstance(files, dict) or not files or set(files) - NAMES[provider]:
            raise ValueError('Fichiers de connexion invalides.')
        if any(not isinstance(v, str) or not v or '\x00' in v for v in files.values()):
            raise ValueError('Contenu de connexion invalide.')
        files = files.copy()
        if provider == 'tiddl':
            auth = json.loads(files['auth.json'])
            if not isinstance(auth, dict) or not all(isinstance(auth.get(k), str) and auth[k] for k in ('token', 'refresh_token')):
                raise ValueError('Session TIDAL invalide.')
        elif provider == 'beatport':
            if not all(files.get(k) for k in ('beatport_username', 'beatport_password')):
                raise ValueError('Compte Beatport incomplet.')
            if 'beatportdl-credentials.json' in files and not isinstance(json.loads(files['beatportdl-credentials.json']), dict):
                raise ValueError('Session Beatport invalide.')
        else:
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(files['config.ini'])
            values = config.defaults()
            if not values.get('user_auth_token') or not values.get('user_id', '').isdigit():
                raise ValueError('Session Qobuz invalide.')
            # Do not import server paths, downloader commands or output preferences.
            safe = configparser.ConfigParser(interpolation=None)
            safe['DEFAULT'] = {k: values[k] for k in ('user_id', 'user_auth_token', 'app_id', 'secrets', 'musicdl_login_id') if k in values}
            output = io.StringIO(); safe.write(output)
            files['config.ini'] = output.getvalue()
        cleaned[provider] = files
    return cleaned


def register(app, api):
    async def page(request):
        return web.FileResponse(api.ASSET_ROOT / 'account-transfer.html', headers={'Cache-Control': 'no-store'})

    async def script(request):
        return web.FileResponse(api.ASSET_ROOT / 'account-transfer.js')

    async def stylesheet(request):
        return web.FileResponse(api.ASSET_ROOT / 'account-transfer.css')

    async def login(request):
        import asyncio
        import time
        payload = await api.read_json_object(request)
        email, password = payload.get('email'), payload.get('password')
        if not isinstance(email, str) or not isinstance(password, str):
            raise api.PublicError('Identifiants invalides.', status=401)
        user = await asyncio.to_thread(api.local_login_user, email.strip().lower(), password)
        if not user:
            raise api.PublicError('Identifiants invalides.', status=401)
        if not user.get('approved') or not user.get('email_verified') or user.get('blocked'):
            raise api.PublicError('Compte non autorisé par l’administrateur.', status=403)
        session = api.session_payload(user)
        # Five-minute session, no persistent refresh token for an export.
        api.local_delete_session(session['refresh_token'])
        with api.local_db() as db:
            db.execute('update sessions set expires_at=? where token_hash=?', (time.time()+300, api.token_hash(session['access_token'])))
        return web.json_response({'access_token':session['access_token']}, headers={'Cache-Control': 'no-store'})

    def approved_user(request):
        # Recheck the database, bypassing the normal 60-second authentication cache.
        token = request.headers.get('Authorization', '')[7:].strip()
        user = api.local_user_for_access_token(token)
        if not user or not user.get('approved') or not user.get('email_verified') or user.get('blocked'):
            raise api.PublicError('Ce compte n’est pas autorisé à récupérer les connexions. Contacte l’administrateur.', status=403)
        return user

    async def session_info(request):
        user = approved_user(request)
        return web.json_response({"email": user["email"]}, headers={'Cache-Control': 'no-store'})

    async def export(request):
        approved_user(request)
        paths = {'tiddl': [api.TIDAL_AUTH_PATH], 'beatport': [api.musicdl.SECRETS_ROOT / 'beatport_username', api.musicdl.SECRETS_ROOT / 'beatport_password', api.BEATPORT_CACHE_PATH], 'qobuz': [api.musicdl.QOBUZ_CONFIG_PATH]}
        payload = await api.read_json_object(request)
        selected = payload.get('providers')
        if not isinstance(selected, list) or not selected or any(not isinstance(p, str) or p not in paths for p in selected):
            raise api.PublicError('Choisis au moins une plateforme.')
        try:
            with ExitStack() as locks:
                for provider in sorted(set(selected)):
                    locks.enter_context(api.musicdl.provider_lock(provider, 'operation', blocking=False))
                accounts = {p: {path.name: path.read_text() for path in paths[p] if path.is_file()} for p in selected}
                bundle = {'format': FORMAT, 'accounts': accounts}
                bundle['accounts'] = validate(bundle)
        except (ValueError, KeyError, OSError, RuntimeError, configparser.Error):
            raise api.PublicError('Connexions indisponibles ou plateforme occupée. Réessaie ou choisis une autre plateforme.', status=409)
        return web.json_response(bundle, headers={'Cache-Control': 'no-store', 'Content-Disposition': 'attachment; filename="tunesaver-accounts.json"'})

    app.router.add_get('/auth/', page)
    app.router.add_get('/account-transfer.js', script)
    app.router.add_get('/account-transfer.css', stylesheet)
    app.router.add_post('/api/account-export', export)
    app.router.add_get('/api/account-transfer/session', session_info)
    app.router.add_post('/api/account-transfer/login', login)
