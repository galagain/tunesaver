"""Public command router. Parsing/help never starts workers or modifies accounts."""
import os
from pathlib import Path
import subprocess
import sys
from tunesaver_version import VERSION

HELP = '''TuneSaver — musique, comptes et bibliothèque

  tunesaver login                 Connecter ses plateformes depuis le web
  tunesaver login tidal           Connecter une plateforme manuellement
  tunesaver <lien>                 Télécharger un morceau, album ou playlist
  tunesaver search <artiste titre> Rechercher un morceau
  tunesaver status                Voir les plateformes connectées
  tunesaver sync                   Synchroniser titres, artistes et liens
  tunesaver install               Installer la commande dans le terminal
  tunesaver server <commande>     Gérer une installation Docker

Sans argument : recherche et téléchargement interactifs.
Serveur : setup, start, stop, status, logs, doctor, retry, build, database.
'''


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('--version', '-V'):
        print('TuneSaver '+VERSION); return 0
    if argv and argv[0] in ('--help', '-h', 'help'):
        print(HELP); return 0
    root = Path(__file__).resolve().parents[1]
    if argv and argv[0] == 'server':
        script = root/'scripts/server.sh'
        if not script.is_file():
            print('Les commandes serveur s’utilisent depuis le dépôt TuneSaver. Voir docs/server.md.',file=sys.stderr);return 2
        arguments = argv[1:] or ['--help']
        if arguments[0] == 'database':
            import server_database
            try: return server_database.main(root, arguments[1:])
            except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
                print(str(exc), file=sys.stderr); return 1
        if arguments[0] in ('setup', 'login'):
            if any(arg in ('--help','-h') for arg in arguments[1:]):
                print('tunesaver server '+arguments[0]+' : connexion web et configuration des plateformes'+(' puis démarrage Docker.' if arguments[0]=='setup' else '.'));return 0
            import server_setup
            try: return server_setup.main(root,start=arguments[0]=='setup')
            except (RuntimeError,ValueError,OSError,KeyboardInterrupt) as exc:
                print('Configuration interrompue.' if isinstance(exc,KeyboardInterrupt) else str(exc),file=sys.stderr);return 1
        arguments = ['tiddl' if v == 'tidal' else v for v in arguments]
        return subprocess.call(['bash',str(script),*arguments])
    # Service entry points are shared by Docker and systemd, not a second CLI.
    if argv and argv[0] == 'serve':
        import webapp
        webapp.main(); return 0
    if argv and argv[0] in ('worker','doctor','enqueue','enqueue-file','run','retry','auth-tiddl','auth-qobuz','versions'):
        import download_engine
        return download_engine.main(argv)
    if os.environ.get('TUNESAVER_RUNTIME') == 'service':
        import service_cli
        try: return service_cli.main(argv)
        except (RuntimeError,ValueError,OSError,KeyboardInterrupt):
            print('Commande interrompue ou service indisponible. Vérifie la connexion et les droits sur les fichiers de configuration.',file=sys.stderr);return 1
    if (root/'python/bin/python3.13').exists():
        import terminal
        if argv and argv[0] == 'login' and len(argv) == 1: argv.append('--web')
        return terminal.main(argv)
    # A checkout can use the installed portable client without importing SDKs.
    installed = Path('/Applications/TuneSaver.app/Contents/Resources/tunesaver')
    if sys.platform == 'darwin' and installed.is_file():
        return subprocess.call([str(installed),*argv])
    if argv:
        print('Le client local n’est pas installé. Utilise une version distribuée de TuneSaver ; pour Docker : ./tunesaver server setup.',file=sys.stderr)
        return 2
    print(HELP);return 0


if __name__ == '__main__':
    raise SystemExit(main())
