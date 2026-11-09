"""Docker onboarding. Never sources .env as shell code or overwrites connected accounts."""
import configparser
import getpass
import json
import os
from pathlib import Path
import subprocess
import tempfile
import account_transfer
import web_login


def write_private(path, text):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.tunesaver-')
    try:
        with os.fdopen(fd,'w') as f:f.write(text)
        os.chmod(name,0o600);os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def apply_bundle(root, bundle):
    accounts=account_transfer.validate(bundle)
    paths={'tiddl':{'auth.json':root/'data/state/tiddl/auth.json'},'beatport':{'beatport_username':root/'secrets/beatport_username','beatport_password':root/'secrets/beatport_password','beatportdl-credentials.json':root/'data/state/beatportdl/beatportdl-credentials.json'},'qobuz':{'config.ini':root/'data/state/qobuz/.config/qobuz-dl/config.ini'}}
    imported=[]
    for provider, files in accounts.items():
        targets=paths[provider]
        required=[targets[name] for name in files if name!='beatportdl-credentials.json']
        if all(p.is_file() and p.stat().st_size for p in required):continue
        for name,value in files.items():write_private(targets[name],value)
        imported.append(provider)
    if 'qobuz' in accounts:
        parser=configparser.ConfigParser(interpolation=None)
        parser.read(paths['qobuz']['config.ini'])
        for name,key in (('qobuz_user_id','user_id'),('qobuz_token','user_auth_token')):
            target=root/'secrets'/name
            if not target.is_file() or not target.stat().st_size:
                write_private(target,parser.defaults()[key])
    if 'sync' in bundle:
        config=bundle['sync']
        if not isinstance(config,dict) or config.get('server')!=web_login.SERVER or not isinstance(config.get('token'),str) or not 30<=len(config['token'])<=200:
            raise ValueError('Configuration de synchronisation invalide.')
        write_private(root/'data/state/catalog/client.json',json.dumps(config))
        env=root/'.env';lines=env.read_text().splitlines()
        lines=[line for line in lines if not line.startswith('TUNESAVER_CATALOG_CONFIG=')]
        lines.append('TUNESAVER_CATALOG_CONFIG=/state/catalog/client.json')
        write_private(env,'\n'.join(lines)+'\n')
    return imported


def main(root, *, start=False):
    root=Path(root);script=root/'scripts/server.sh'
    env_existed=(root/'.env').exists()
    subprocess.run(['bash',str(script),'init','--skip-beatport','--skip-qobuz'],check=True)
    if not env_existed:
        password=getpass.getpass('Choisis le mot de passe administrateur du site (Entrée : garder celui généré dans .env) : ')
        if password:
            # Compose does not interpolate variables inside single-quoted values.
            encoded = "'" + password.replace("\\", "\\\\").replace("'", "\\'") + "'"
            env=root/'.env';lines=env.read_text().splitlines()
            lines=[('ADMIN_PASSWORD='+encoded) if line.startswith('ADMIN_PASSWORD=') else line for line in lines]
            write_private(env,'\n'.join(lines)+'\n')
    accounts=apply_bundle(root,web_login.login())
    print('Plateformes configurées : '+(', '.join(accounts) or 'comptes existants conservés'))
    if start:return subprocess.call(['bash',str(script),'start'])
    print('Lance tunesaver server start pour démarrer les services.');return 0
