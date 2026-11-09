"""TuneSaver-owned PostgreSQL setup and private backups."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import subprocess


def main(root, argv):
    parser = argparse.ArgumentParser(prog='tunesaver server database')
    parser.add_argument('action', choices=('setup', 'backup'))
    args = parser.parse_args(argv)
    root = Path(root)
    if args.action == 'setup':
        config = root / '.env.database'
        if not config.exists():
            sqlite = root / 'data/state/tiddl/tunesaver.db'
            if sqlite.exists() and sqlite.stat().st_size:
                raise RuntimeError('Une base SQLite existe. Effectue sa migration avant de sélectionner PostgreSQL ; voir docs/postgres.md.')
            from server_setup import write_private
            owner = root / 'secrets/postgres_password'
            if not owner.exists():
                write_private(owner, secrets.token_urlsafe(48))
            write_private(config, 'TUNESAVER_PG_HOST=postgres\nTUNESAVER_PG_DATABASE=tunesaver\nTUNESAVER_PG_USER=tunesaver_app\nTUNESAVER_PG_PASSWORD='+secrets.token_urlsafe(48)+'\n')
        if not (root / 'secrets/postgres_password').is_file():
            raise RuntimeError('La configuration PostgreSQL existe mais son secret propriétaire manque. Consulte docs/postgres.md avant de continuer.')
        return subprocess.call(['docker','compose','--project-directory',str(root),'-f',str(root/'compose.yaml'),'-f',str(root/'compose.postgres.yaml'),'up','-d','--wait','postgres'])
    folder = root / 'data/backups'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    target = folder / ('tunesaver-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.dump')
    temporary = target.with_suffix('.partial')
    try:
        with os.fdopen(os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as out:
            subprocess.run(['docker','compose','--project-directory',str(root),'-f',str(root/'compose.yaml'),'-f',str(root/'compose.postgres.yaml'),'exec','-T','postgres','pg_dump','-U','tunesaver_owner','-d','tunesaver','--format=custom','--no-owner','--no-acl'],stdout=out,check=True)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print(target)
    return 0
