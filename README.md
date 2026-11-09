# TuneSaver

Bibliotheque musicale web avec playlists, lecture audio, recherche et import
de morceaux depuis TIDAL, Beatport et Qobuz. Les comptes sont approuves par
l'administrateur ; inscriptions et recuperations utilisent des codes par e-mail.

Le projet contient une application web avec workers Docker et une application
macOS autonome avec une commande `tunesaver` utilisable sans ouvrir l'application.
La version Mac utilise les comptes personnels de chaque
utilisateur et stocke sa bibliothèque sur son ordinateur, sans serveur TuneSaver.
Voir [la version macOS](docs/macos.md) pour l'installation, la construction et
les commandes terminal et les limites de cette version.

Après installation de la commande sur Mac :

```sh
tunesaver login tidal
tunesaver                       # coller un lien ou chercher un titre
tunesaver "https://tidal.com/track/123"
```

Les téléchargements terminal sont enregistrés dans `~/Music/TuneSaver`.

## Installation

Prerequis : Docker avec Compose v2, Bash, une connexion Internet et les comptes
adaptes aux plateformes. Utiliser uniquement des contenus que vous avez le droit
de telecharger et respecter les conditions des services.

```sh
git clone https://github.com/galagain/tunesaver.git
cd tunesaver
./musicdl init
```

Configurer `.env` : administrateur, SMTP, chemins de stockage et base de donnees.
Le deploiement Compose utilise un reseau externe AppSilo : suivre le
[guide PostgreSQL](docs/appsilo.md) avant de demarrer. Le moteur conserve un
backend SQLite pour les tests et le developpement sans PostgreSQL.

```sh
./musicdl auth tiddl
./musicdl auth qobuz
./musicdl start
./musicdl doctor
```

Interface : <http://127.0.0.1:8080> (`WEB_PORT` dans `.env`). Pour l'acces distant,
utiliser un reverse proxy HTTPS et renseigner son domaine dans
`TUNESAVER_ALLOWED_HOSTS`. Ne pas exposer directement le port HTTP sur Internet.

## Utilisation

- Rechercher un titre ou coller des liens de morceaux, playlists et albums.
- Importer depuis Spotify, Apple Music, SoundCloud et les trois catalogues cibles.
- Apparier dans l'ordre TIDAL, Beatport, puis Qobuz, avec verification de la
  version, des artistes et de la duree. Les liens natifs evitent la recherche.
- Organiser les morceaux en playlists, les ecouter ou recuperer une playlist en ZIP.
- Exporter et reimporter les playlists au format texte, y compris par glisser-deposer.
- Consulter les echecs, les comptes, l'activite et les retours utilisateurs dans l'admin.

Les fichiers audio des imports web expirent apres sept jours. Les titres,
playlists et liens sont conserves pour permettre un nouveau telechargement.
Voir [la retention audio](docs/audio-retention.md) pour le perimetre exact.

## Exploitation

```sh
./musicdl status
./musicdl logs
./musicdl retry all
./musicdl stop
```

Pour changer de compte plateforme, arreter les services, executer la commande
concernee, puis relancer `./musicdl start` :

```sh
./musicdl auth tiddl --force
./musicdl auth qobuz --force
./musicdl init --force-beatport --skip-qobuz
```

TIDAL affiche un lien d'autorisation. Qobuz demande l'identifiant et le jeton de
session du lecteur officiel. Ne jamais partager ces valeurs.

Mise a jour du deploiement standard :

```sh
git pull --ff-only
./musicdl build
./musicdl start
```

Le `Dockerfile` principal permet une construction complete. Les variantes
`Dockerfile.appsilo` et `Dockerfile.matching` sont des accelerateurs propres au
serveur existant, pas des prerequis pour cloner le projet.

## Donnees Privees

Ne pas versionner `.env`, les fichiers de connexion PostgreSQL, `secrets/` ni
le contenu de `data/`. Les configurations publiques sont dans `config/`.

- `data/queue/` : taches persistantes et journaux.
- `data/deliveries/` : fichiers des utilisateurs du site.
- `data/downloads/` : sorties des outils en ligne de commande.
- `data/state/` : sessions et caches des plateformes.
- PostgreSQL : comptes, sessions TuneSaver, bibliotheques et historique.

Sauvegarder separement la base, les fichiers et les secrets. GitHub ne constitue
pas une sauvegarde des donnees des utilisateurs. Arreter les conteneurs ne les efface pas.

## Developpement Et Tests

```sh
# Tests isoles, sans comptes reels ni telechargements
docker run --rm -v "$PWD:/repo:ro" -w /repo --entrypoint python \
  tunesaver:local -m unittest discover -s tests

# Diagnostics reels : plateformes configurees et serveur demarre requis
python3 test_playback.py --docker
python3 test_downloads.py --docker
```

`test_playback.py` verifie des fichiers existants. `test_downloads.py` lance de
vrais telechargements isoles et nettoie ses fichiers de test.

Les scripts `export_playlist.py`, `match_music.py`, `run_playlist_tests.py` et
`run_matching_tests.py` permettent de tester l'extraction et l'appariement sans
passer par l'interface. Les tests navigateur utilisent Playwright.

## Documentation

- [PostgreSQL, migration et sauvegardes](docs/appsilo.md)
- [Moteur d'appariement](docs/music-matching.md)
- [Tests de playlists](docs/playlist-tests.md)
- [Tests d'appariement](docs/matching-tests.md)
- [Visualisation des tests](docs/matching-live.md)
- [Format texte des playlists](docs/playlist-text.md)
- [Lecture audio](docs/player.md)
- [Retention des fichiers](docs/audio-retention.md)

Les versions des outils tiers et leurs empreintes sont definies dans `Dockerfile`
et les fichiers `requirements*.lock`. Leurs licences sont conservees dans l'image.
