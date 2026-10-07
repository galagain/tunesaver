# Imports du site

Le site et les scripts utilisent `catalog_matcher` pour les correspondances et
les memes lecteurs de metadonnees pour les playlists, albums, liens et saisies
`Artiste - Titre`.

1. Importer une liste affiche ses metadonnees sans chercher ni telecharger.
2. Choisir le nombre de titres (0 = tous), puis rechercher seulement ou rechercher
   et telecharger. L'ordre est TIDAL, Beatport, Qobuz.
3. Les albums sont recherches entiers avant le repli morceau par morceau.
   Les liens natifs valides evitent la recherche. Le nettoyage SoundCloud est partage.
4. La recherche seule conserve ses correspondances pour un telechargement ulterieur
   sans nouvelle recherche. Le resultat JSON reprend le schema des scripts.

La progression et les correspondances sont sauvegardees dans les lignes d'import
existantes, sans migration SQL. Les listes completes restent consultables pendant
le traitement, y compris les titres exclus par la limite. Le site actualise son
suivi toutes les cinq secondes. Arreter un import bloque les recherches et les
prochains ajouts ; les telechargements deja mis en file restent independants.

Un redemarrage reprend les imports en cours. Les ajouts sont dedupliques par
utilisateur et URL. Les anciens imports en echec ne sont pas relances automatiquement.
Le serveur des scenarios de test et tqdm restent des outils de developpement,
non exposes aux utilisateurs du site.

## Deploiement applicatif

Le Dockerfile principal inclut tous les nouveaux modules. Sur le serveur ayant
deja l'image `tunesaver:appsilo`, la variante suivante reutilise son environnement :

```sh
docker compose -f compose.yaml -f compose.matching.yaml build web
docker compose -f compose.yaml -f compose.matching.yaml up -d --no-deps --no-build web
```

Ne pas reconstruire ni redemarrer les workers audio pour cette mise a jour.
Conserver l'image precedente pour un retour arriere. Les donnees et les comptes
ne sont ni effaces ni migres par cette operation.
