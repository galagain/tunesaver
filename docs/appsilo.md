# TuneSaver avec PostgreSQL et AppSilo

TuneSaver conserve son API aiohttp, son interface, son authentification et ses processus de téléchargement. AppSilo fournit une base PostgreSQL privée, un rôle propriétaire pour les migrations, un rôle limité pour l'application et les sauvegardes de la base. Le modèle FastAPI de AppSilo n'est pas utilisé pour TuneSaver.

## Configuration

Provisionner la base avec `appsilo.py create tunesaver --database-only`. Copier les paramètres dans un fichier privé extérieur au dépôt, lisible par l'utilisateur Docker, avec le mode `0600` :

```dotenv
TUNESAVER_PG_HOST=appsilo-db
TUNESAVER_PG_DATABASE=tunesaver
TUNESAVER_PG_USER=tunesaver_app
TUNESAVER_PG_PASSWORD=secret-fourni-par-appsilo
```

Configurer dans le `.env` privé de TuneSaver :

- `TUNESAVER_DATABASE_ENV` : chemin absolu de ce fichier.
- `APPSILO_DB_NETWORK` : valeur `DB_NETWORK` fournie par AppSilo.
- `TUNESAVER_WEB_IMAGE` : image web à démarrer.

Le Compose connecte uniquement le service web au réseau PostgreSQL, en conservant son réseau habituel et son port HTTP local. Le fichier d'environnement de l'application ne doit jamais contenir le mot de passe propriétaire. L'absence de `TUNESAVER_PG_HOST` active SQLite ; une connexion PostgreSQL configurée mais indisponible provoque une erreur, sans bascule silencieuse vers SQLite.

## Schéma et migration initiale

`migrations/001_postgres.sql` décrit le schéma complet, y compris les tables historiques. Les horodatages numériques utilisent `double precision`, pour conserver la précision de SQLite. Les tables de morceaux de playlists et tags conservent un numéro d'insertion explicite afin de préserver l'ordre des morceaux partageant la même date.

`tools/migrate_postgres.py` importe une copie SQLite vers une base PostgreSQL vide. Il s'exécute avec le rôle propriétaire et des variables `TUNESAVER_PG_*` correspondantes. Exemple dans un conteneur ayant accès au réseau PostgreSQL et à une copie SQLite montée en lecture seule :

```bash
python /app/migrate_postgres.py /migration/final.sqlite \
  --runtime-role tunesaver_app --report /migration/report.json
```

L'import refuse une base non vide, contrôle l'intégrité SQLite et ses clés étrangères, crée le schéma et copie les données dans une transaction PostgreSQL. Pour chaque table, les lignes source et destination sont comparées par empreintes SHA-256 indépendantes de leur ordre physique. Une différence annule la transaction. Seuls les comptes, sessions et données de l'application sont copiés ; les fichiers audio restent à leur emplacement.

Après la migration, le démarrage vérifie la version du schéma. Il ne réalise aucune modification de structure avec le rôle applicatif. Les futures évolutions de schéma devront fournir une migration explicite exécutée avec le rôle propriétaire.

Pour la première transition depuis SQLite : sauvegarder les configurations et l'image web existantes, répéter la migration sur une copie cohérente, arrêter le web, prendre une nouvelle copie via l'API SQLite `backup`, importer cette copie finale, puis redémarrer le web avec PostgreSQL. Les workers écrivent dans les fichiers et les files de tâches ; ils peuvent rester actifs pendant cette transition.

## Sauvegardes et récupération

```bash
python /chemin/appsilo/appsilo.py backup tunesaver
python /chemin/appsilo/appsilo.py restore-check tunesaver /chemin/fichier.dump
```

La sauvegarde quotidienne `backup-all` de AppSilo inclut TuneSaver. Les dumps couvrent toutes ses tables, dont comptes, sessions, historique, playlists et analyses. Ils ne couvrent pas les fichiers de `data/`, les secrets des fournisseurs, la configuration SMTP ou le code applicatif : conserver ces éléments séparément.

Pour une récupération, créer une nouvelle base en mode `--database-only`, restaurer le dump avec AppSilo puis réappliquer les droits du rôle applicatif. Le module `/app/migrate_postgres.py` fournit `grant(connection, runtime_role)` ; l'appeler avec une connexion propriétaire vers la base restaurée. Vérifier ensuite la version du schéma, les comptes, bibliothèques et fichiers avant de changer la connexion du service web. Les sessions contenues dans le dump sont conservées ; pour imposer une reconnexion après incident, vider `sessions` et `stream_tickets` avant d'ouvrir le service.

L'ancienne base SQLite est un point de retour correspondant à l'instant de bascule. Après de nouvelles écritures PostgreSQL, redémarrer simplement sur SQLite ferait perdre ces nouvelles données : toute marche arrière nécessite alors une réconciliation ou une migration inverse.

## Validation

Les tests existants restent exécutables sur SQLite. `tests/run_postgres.py` exécute les tests HTTP sur une base jetable dont le nom finit par `_test`. Il recrée son schéma et réinitialise ses tables entre tests : ne jamais lui fournir une base contenant des données à conserver. Il reçoit les paramètres du rôle applicatif et `TUNESAVER_TEST_OWNER_PASSWORD` pour les seules opérations de préparation.

La couche de compatibilité utilise des paramètres liés pour les valeurs SQL et conserve des transactions sérialisées via un verrou PostgreSQL. Ce choix préserve les comportements de concurrence existants pendant la migration ; l'optimisation vers des verrous plus fins nécessitera des tests de charge.

La construction normale utilise `Dockerfile`. `Dockerfile.appsilo` est seulement un accélérateur pour le serveur où l'image de production antérieure a été conservée sous `tunesaver:before-appsilo` ; il ne sert pas à une construction autonome depuis GitHub.
