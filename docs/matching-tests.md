# Scenarios d'appariement

`run_matching_tests.py` est la seconde etape de `run_playlist_tests.py`.
Il reutilise la meme liste de 19 scenarios mais travaille sur le fichier
`inputs.json` deja exporte. Il ne relit pas les playlists sur les plateformes.

```sh
python3 run_matching_tests.py --docker
```

Sans `--input`, le dernier fichier `data/playlist-tests/*/inputs.json` est choisi.
Les rapports d'appariement ne sont pas reutilises automatiquement comme entrees.
Pour choisir explicitement un export :

```sh
python3 run_matching_tests.py --docker --input data/playlist-tests/RUN/inputs.json
```

Pour ne lancer que certains scenarios :

```sh
python3 run_matching_tests.py --docker --case spotify-album --case beatport --case manual-track
```

Identifiants : nom de source pour les playlists (`spotify`, `applemusic`,
`soundcloud`, `beatport`, `tiddl`, `qobuz`), nom suivi de `-album` ou `-track`
pour les autres liens. `manual-track` correspond au nom saisi manuellement.
Les identifiants inconnus sont refuses avant toute recherche.

## Regles

- Les liens TIDAL, Beatport et Qobuz deja resolus sont verifies et conserves.
  Aucune recherche de remplacement n'est lancee pour eux.
- Les autres titres sont recherches dans l'ordre TIDAL, Beatport, Qobuz.
- Les albums sont cherches entiers sur chaque plateforme, puis les morceaux
  restants individuellement si aucun album complet ne correspond.
- L'artiste, le titre, la version et la duree disponible servent a valider les
  candidats, via le meme moteur que `match_music.py`.
- Les titres introuvables, ambigus, indisponibles ou en erreur restent presents.
  Un scenario absent de l'export est indique en echec, jamais ignore.
- Aucun fichier audio, aucune file de telechargement et aucune base utilisateur
  ne sont touches. Les sessions existantes des services sont necessaires.

## Fichiers produits

Un dossier unique est cree dans `matching/` a cote de l'entree. `--output-dir`
permet de choisir un autre dossier parent. L'export source n'est pas modifie.

- Un JSON et un TXT par scenario execute correctement.
- `inputs.json` conserve le schema des collections et l'ordre des morceaux.
  Chaque cible trouvee complete `target` et met `requires_matching` a `false`.
  `target.method` vaut `direct`, `album` ou `track`.
- Chaque morceau possede un objet `matching` avec statut, candidat retenu et
  tentatives. La collection ajoute le bilan et les tentatives d'album.
- `report.json` distingue scenarios traites et entierement apparies, avec le
  nombre de titres trouves et les echecs. `planned` indique le nombre prevu.
- `viewer.html` affiche les scenarios et leurs cibles dans la meme interface.
  Les liens des morceaux pointent vers les resultats natifs retenus.

Le TXT conserve la liste lisible des titres d'origine ; les decisions et liens
apparies sont dans les JSON et la visualisation. Les resultats sont sauvegardes
apres chaque scenario : une interruption conserve ceux deja termines. Le
scenario en cours doit etre relance, il n'est pas automatiquement repris.

Code de sortie : 0 si tous les scenarios selectionnes sont entierement apparies,
1 si au moins un scenario est incomplet ou en echec, 2 pour une erreur de commande,
130 pour une interruption. Un titre introuvable ne signifie pas qu'il est absent
de tout le catalogue, seulement qu'aucun candidat fiable n'a ete retenu.

Les grandes playlists peuvent demander de nombreuses requetes. Aucun plafond
de taille n'est ajoute ; utiliser `--case` pour les essais cibles. Sans `--docker`,
le script necessite l'environnement Python et les sessions TuneSaver configures.

## Tests hors reseau

```sh
python -m unittest discover -s tests -p test_matching_runner.py
```

Les fixtures couvrent les 19 configurations, la conservation des liens natifs,
le rejet des cibles falsifiees, l'ordre, les doublons, les erreurs et la sauvegarde.
