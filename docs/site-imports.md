# Imports du site

Le site et les scripts utilisent `catalog_matcher` pour les correspondances et
les memes lecteurs de metadonnees pour les playlists, albums, liens et saisies
`Artiste - Titre`.

1. Coller un lien dans le champ de recherche lance automatiquement la lecture,
   quel que soit son type (morceau, album ou playlist). Un lien saisi au clavier
   se valide avec Entree. Le site affiche les metadonnees sans chercher ni telecharger.
   La recherche textuelle reste disponible dans ce meme champ.
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

### Repli apres un echec de telechargement

Le serveur web examine toutes les dix secondes les echecs des dernieres 24 heures.
Pour un morceau individuel d'un utilisateur approuve, une qualite indisponible ou
la reponse JSON invalide observee chez TIDAL declenche une recherche sur les autres
plateformes actives. Les plateformes deja essayees sont exclues. La version et la
duree sont comparees aux metadonnees du morceau initial, meme apres plusieurs replis.
L'identifiant, le proprietaire et les appartenances aux playlists sont conserves.
Les raisons et liens des tentatives precedentes restent dans le fichier de tache.
Une recherche sans remplacement est marquee comme traitee pour eviter une boucle.
Les erreurs de disque et les autres erreurs non reconnues ne sont pas relancees.
Les qualites configurees de chaque plateforme restent inchangees : ce mecanisme
ne garantit pas une qualite lossless sur la plateforme de remplacement.

Le Dockerfile unique inclut les modules d’import et de recherche.
La mise à jour se fait avec `./tunesaver server start`.
