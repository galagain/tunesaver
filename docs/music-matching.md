# Appariements sans telechargement

Depuis le dossier TuneSaver :

```sh
python3 match_music.py --docker "Hot Since 82 - Buggin' (feat. Jem Cooke)" -o matches.json
python3 match_music.py --docker 'https://open.spotify.com/album/4m2880jivSbbyEGAKfITCa' -o album.json
python3 match_music.py --docker --file entrees.txt -o matches.json
```

Plusieurs arguments peuvent etre melanges dans la meme commande. Le fichier
UTF-8 accepte une entree par ligne ; les lignes vides et celles commencant par
`#` sont ignorees. Exemples de contenu :

```text
Hot Since 82 - Buggin' (feat. Jem Cooke)
Daft Punk - Give Life Back to Music
https://open.spotify.com/playlist/57HAIqxdHbi332994xcOKc
https://music.apple.com/us/album/random-access-memories/617154241
https://soundcloud.com/daftpunkofficialmusic/sets/random-access-memories-10th
```

## Entrees

- Playlists : les six sources deja gerees par `export_playlist.py`.
- Albums : Spotify, Apple Music, SoundCloud (sets identifies comme albums),
  TIDAL, Beatport (release) et Qobuz.
- Morceaux : liens complets Spotify, Apple Music (`?i=`), SoundCloud,
  TIDAL, Beatport et Qobuz. Les liens raccourcis ne sont pas resolus.
- Texte : `Artiste - Titre` (ou separateur point median). Un titre seul est
  accepte mais reste ambigu si plusieurs artistes correspondent aux candidats.

## Ordre et validation

Un lien natif TIDAL, Beatport ou Qobuz est prioritaire sur cet ordre : son
identifiant est deja connu. Les metadonnees sont lues directement, sans recherche
de remplacement, et le rapport indique `method: direct`. Cela vaut aussi pour
tous les titres d'un album natif ou d'une playlist native deja resolue.

1. TIDAL, puis Beatport, puis Qobuz. Une fois un morceau apparie, les sources
   suivantes ne sont plus sollicitees pour celui-ci.
2. Pour un album, chaque source est d'abord interrogee sur l'album entier.
   Le nom de l'edition, le nombre de titres et chaque morceau doivent correspondre.
   Les durees disponibles et les versions/remixes sont verifies. Les pistes sont
   remises dans l'ordre source et les doublons conserves.
3. Sans album complet valide, recherche des morceaux restants sur cette meme
   source, avant de passer a la suivante. Des requetes simplifiees conservent
   l'artiste principal et retirent les credits d'invites pour elargir la recherche.

Les credits `feat.` places dans le champ version sont reconnus. Pour SoundCloud,
la duree complete est utilisee, pas celle de l'extrait gratuit de 30 secondes.

Les candidats de recherche proviennent des resultats classes de chaque service,
pas d'un scan exhaustif du catalogue. `not_found` signifie qu'aucun candidat
fiable n'a ete trouve, pas une preuve d'absence du morceau sur la plateforme.
Une recherche d'album peut etre refusee si son edition ou sa liste differe.

## Rapport

JSON avec une section par entree, les tentatives d'album et chaque morceau :
`matched`, `not_found`, `ambiguous`, `unavailable` ou `error`. Un appariement
contient la plateforme, le lien et la methode (`album`, `track` ou `direct`). Les erreurs
de connexion ne sont pas confondues avec une absence de resultat.

`-o -` ecrit le JSON sur stdout ; la progression reste sur stderr.
`--force` autorise le remplacement d'un rapport existant. L'ecriture est atomique.
Code de sortie : 0 si les recherches ont abouti (meme avec des titres non trouves),
1 si une entree ou un titre reste en erreur technique, 2 si la commande echoue,
130 en cas d'interruption.

`--docker` utilise l'image et les sessions du conteneur `tunesaver-web-1` ;
`--container NOM` permet de le changer. Sans Docker, utiliser l'environnement
Python et la configuration TuneSaver. Les sessions doivent etre valides.
Les renouvellements d'authentification peuvent mettre a jour leurs caches.
Une source en panne ou limitee (HTTP 429) n'est pas bombardee de nouvelles
requetes : elle reste indisponible jusqu'a la prochaine execution.

Aucun fichier audio n'est telecharge, aucun job n'est cree, aucun compte ni
playlist n'est modifie en base. Les appels sont limites aux metadonnees ; le
script n'impose pas de plafond au nombre de morceaux de l'entree.

## Tests

```sh
python -m unittest discover -s tests -p test_catalog_matcher.py
```
