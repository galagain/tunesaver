# PostgreSQL TuneSaver

Le déploiement standard utilise SQLite. L’option PostgreSQL utilise un conteneur
`postgres` et le volume `tunesaver_postgres_data`, dans le réseau privé TuneSaver.
Aucun port de base de données n’est publié sur l’hôte.

## Nouvelle installation

Après la préparation du dépôt, avant d’y créer des données :

```sh
./tunesaver server database setup
./tunesaver server setup
```

La première commande crée `.env.database`, le secret propriétaire et le schéma,
puis démarre PostgreSQL. Le site utilise le rôle limité `tunesaver_app`.
Le mot de passe propriétaire reste dans `secrets/postgres_password` et n’est
jamais transmis au conteneur web. Les fichiers de secrets ont les permissions 600.
La présence de `.env.database` sélectionne automatiquement le Compose PostgreSQL.
Une connexion PostgreSQL configurée mais indisponible provoque une erreur :
il n’y a pas de bascule silencieuse vers une autre base SQLite.

## Base existante

La configuration refuse de remplacer automatiquement une base SQLite contenant
des données. Arrêter le site, sauvegarder SQLite avec son API `backup`, puis
utiliser `tools/migrate_postgres.py` vers une base PostgreSQL vide. Cet outil
compare les lignes par empreinte SHA-256 dans une transaction et refuse une base
destination non vide. Appliquer ensuite les migrations supplémentaires et leurs droits.

Pour transférer une base PostgreSQL existante, arrêter les écritures, réaliser un
`pg_dump --format=custom --no-owner --no-acl`, restaurer dans le PostgreSQL TuneSaver
avec le rôle `tunesaver_owner`, puis réappliquer les droits du rôle applicatif.
Comparer les comptes, les tables et les empreintes des données avant de redémarrer
le site. Conserver la sauvegarde et l’ancienne base jusqu’à validation.
Les audios et comptes des plateformes restent à leurs chemins existants.

## Sauvegardes

```sh
./tunesaver server database backup
```

Le dump est écrit dans `data/backups/`, en permissions 600, puis renommé après
réussite. Une erreur ne laisse pas de fichier présenté comme une sauvegarde valide.
Planifier cette commande quotidiennement et copier les sauvegardes hors de la machine.
Sauvegarder également `.env`, `.env.database`, `secrets/` et les fichiers de `data/` :
le dump ne contient pas les fichiers audio ni les connexions des plateformes.

Pour tester la restauration, utiliser une base temporaire distincte et
`pg_restore --exit-on-error --no-owner --no-acl`. Ne jamais tester sur la base active.

## Migrations et tests

Les scripts de `migrations/` sont appliqués au premier démarrage d’un volume vide.
Sur un volume existant, appliquer uniquement les nouvelles migrations avant de
mettre à jour le site, puis réappliquer les droits. `tools/migrate_postgres.py`
expose `grant(connection, runtime_role)` pour les droits des tables existantes.

`tests/run_postgres.py` utilise exclusivement une base jetable dont le nom finit
par `_test`. La suite HTTP SQLite reste disponible pour les tests locaux.
