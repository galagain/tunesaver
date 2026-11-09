"""TuneSaver commands for an installed Linux service (Docker or Raspberry)."""
import json
import os
from pathlib import Path


def main(argv):
    import download_engine
    command = argv[0] if argv else 'status'
    if command == 'login':
        import account_transfer,web_login,server_setup
        bundle=web_login.login();accounts=account_transfer.validate(bundle)
        paths={'tiddl':{'auth.json':download_engine.STATE_ROOT/'tiddl/auth.json'},'beatport':{'beatport_username':download_engine.SECRETS_ROOT/'beatport_username','beatport_password':download_engine.SECRETS_ROOT/'beatport_password','beatportdl-credentials.json':download_engine.STATE_ROOT/'beatportdl/beatportdl-credentials.json'},'qobuz':{'config.ini':download_engine.QOBUZ_CONFIG_PATH}}
        for provider,files in accounts.items():
            if all(p.is_file() and p.stat().st_size for name,p in paths[provider].items() if name in files):continue
            with download_engine.provider_lock(provider,'operation',blocking=False):
                for name,value in files.items():server_setup.write_private(paths[provider][name],value)
        if bundle.get('sync'):
            import catalog_sync
            config=catalog_sync.valid_config(bundle['sync'])
            target=Path(os.environ.get('TUNESAVER_CATALOG_CONFIG') or download_engine.STATE_ROOT/'catalog/client.json')
            server_setup.write_private(target,json.dumps(config))
        print('Comptes connectés.');return 0
    if command == 'sync':
        import catalog_sync
        target=Path(os.environ.get('TUNESAVER_CATALOG_CONFIG') or download_engine.STATE_ROOT/'catalog/client.json')
        if not target.is_file():raise RuntimeError('Connecte d’abord TuneSaver avec tunesaver login.')
        catalog_sync.harvest_local(target.parent,download_engine)
        print(json.dumps(catalog_sync.flush(target.parent,json.loads(target.read_text())),ensure_ascii=False));return 0
    return download_engine.main(argv or ['status'])
