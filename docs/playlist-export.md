# Exporter une playlist sans telecharger les sons

Le script `export_playlist.py` accepte les playlists Spotify, Apple Music,
SoundCloud, Beatport (playlists et charts), TIDAL et Qobuz. Il n'effectue aucune
recherche de correspondance et n'ajoute rien aux comptes TuneSaver.

Depuis le dossier du projet, dans l'environnement Python de TuneSaver :

```sh
python export_playlist.py 'https://open.spotify.com/playlist/57HAIqxdHbi332994xcOKc' -o playlist.txt
python export_playlist.py 'LIEN_PLAYLIST' -o playlist.json
python export_playlist.py 'LIEN_PLAYLIST' -o playlist.csv
python export_playlist.py 'LIEN_PLAYLIST' -o -
```

Sans `-o`, le fichier s'appelle `playlist.txt`. Son extension choisit le format;
`--format txt|json|csv` permet de le specifier. `--force` autorise le remplacement
d'un fichier existant. Sinon, aucun fichier existant n'est ecrase.

Chaque entree contient le numero, le titre, l'artiste et la duree lorsqu'elle est
disponible. JSON et CSV contiennent aussi le lien du titre lorsqu'il est fourni
par le lecteur source, et l'indication d'indisponibilite. L'ordre et les doublons
sont conserves. Aucun plafond de nombre de titres n'est impose par l'exporteur.
Les delais reseau et les controles de completude restent actifs.

## Environnement

Spotify et Apple Music utilisent `requests` et `beautifulsoup4`; SoundCloud
utilise aussi `yt-dlp`. Spotify est lu par HTTP uniquement, sans navigateur.
Les versions des dependances du projet figurent dans `requirements.lock`.

Beatport, TIDAL et Qobuz reutilisent les lecteurs et les sessions du serveur.
Ils necessitent l'environnement TuneSaver configure, y compris le binaire
`beatport-search` et le module Qobuz. Le script ne contourne pas les restrictions
de playlists privees, supprimees ou inaccessibles depuis le compte configure.
Les autres plateformes ne sont pas prises en charge et renvoient une erreur.

Sur le serveur Docker existant, sans installer les dependances sur l'hote :

```sh
python3 export_playlist.py 'LIEN_PLAYLIST' --docker -o playlist.txt
```

L'option `--docker` utilise l'image du conteneur `tunesaver-web-1` et ses volumes;
`--container NOM` permet de changer le conteneur de reference. Elle necessite
les permissions Docker. Le resultat est ecrit par le script sur l'hote.
Le conteneur accede aux sessions existantes; leur rafraichissement peut modifier
les caches d'authentification. L'exporteur ne lance pas de serveur, n'accede pas
a la base de donnees et ne telecharge aucun fichier audio. Pour une sortie JSON,
utiliser `-o playlist.json`.

Une lecture incomplete ou une erreur renvoie un code de sortie non nul.
Avec `-o fichier`, le resultat n'est publie qu'apres lecture et ecriture completes.
