"""Portable browser sign-in client. Standard library only; credentials stay in memory."""
import json
import time
import urllib.error
import urllib.request
import webbrowser

from tunesaver_version import VERSION

SERVER='https://tunesaver.galagain.com'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


def post(path, body):
    request=urllib.request.Request(SERVER+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','User-Agent':'TuneSaver/'+VERSION,'Accept':'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=20) as response:
            content=response.read(256*1024+1)
            if len(content)>256*1024:raise RuntimeError('Réponse du serveur trop volumineuse.')
            return response.status,json.loads(content)
    except (urllib.error.URLError,ValueError,OSError) as exc:
        # Never print exception URLs or bodies that might carry credentials.
        if isinstance(exc,urllib.error.HTTPError) and exc.code in (403,410):
            raise RuntimeError('Connexion refusée ou expirée. Relance tunesaver login.') from None
        raise RuntimeError('Serveur TuneSaver inaccessible. Réessaie dans un instant.') from None


def start():
    _,grant=post('/api/device/start',{})
    if not isinstance(grant.get('device_token'),str) or not isinstance(grant.get('code'),str) or grant.get('verification_uri')!=SERVER+'/auth/?device='+grant['code']:
        raise RuntimeError('Réponse de connexion invalide.')
    return grant


def wait(grant, cancel=None):
    end=time.monotonic()+600
    while time.monotonic()<end:
        if cancel is not None:
            if cancel.wait(3): raise RuntimeError("Connexion annulée.")
        else: time.sleep(3)
        status,bundle=post('/api/device/poll',{'device_token':grant['device_token']})
        if status==200:
            import account_transfer
            account_transfer.validate(bundle)
            return bundle
        if status!=202:raise RuntimeError('Connexion indisponible. Relance tunesaver login.')
    raise RuntimeError('Connexion expirée. Relance tunesaver login.')


def login():
    grant=start()
    print('Ouvre '+grant['verification_uri']+'\nCode : '+grant['code'],flush=True)
    print('Valide « Connecter cet appareil » dans le navigateur.',flush=True)
    try:webbrowser.open(grant['verification_uri'])
    except Exception:pass
    return wait(grant)
