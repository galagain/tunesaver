# Architecture TuneSaver

## Entrées publiques

`src/cli.py` route toutes les commandes. Le lanceur du dépôt, l’application portable,
le terminal installé et l’entrée Docker portent le même nom : `tunesaver`.

- `terminal.py` : expérience locale du terminal Mac, téléchargements et recherche.
- `desktop.py` : hôte Mac, API locale, comptes et workers ; mêmes données et Trousseau que le terminal.
- `service_cli.py` : commandes des services Linux/Docker avec leurs chemins configurés.
- `scripts/server.sh` : exploitation Docker, appelée par `tunesaver server`.
- `server_setup.py` : installation et connexion Docker, sans modifier les comptes existants.
- `webapp.py` : application web et droits utilisateurs/admin.

## Fonctions partagées

- `web_login.py` : client de connexion par navigateur, sans dépendance externe.
- `device_login.py` : autorisations d’appareil de dix minutes, usage unique.
- `account_transfer.py` : validation et export contrôlé des comptes, utilisé par le fichier manuel et la connexion directe.
- `catalog_sync.py` : métadonnées uniquement, file locale durable et catalogue global.
- `download_engine.py` : moteur de téléchargement et de gestion des files ; module interne partagé.

Le moteur de téléchargement est `download_engine.py`. Les paramètres utilisent
exclusivement le préfixe `TUNESAVER_`. Les chemins de données sont conservés.
Un seul Dockerfile construit toutes les installations.

## Connexion web

L’appareil conserve un secret aléatoire de connexion, dont le serveur stocke uniquement
l’empreinte. Le navigateur reçoit un code distinct. Un utilisateur déjà connecté et
approuvé confirme explicitement le code et les plateformes. L’appareil récupère alors
les connexions par HTTPS, une seule fois. Les autorisations expirées ou déjà consommées
sont refusées et l’approbation du compte est revérifiée à la récupération. Le navigateur
ne reçoit pas les fichiers de connexion dans ce parcours.

PostgreSQL : `004_device_login.sql` complète `003_catalog.sql`. Les migrations doivent
être appliquées avant de déployer le code correspondant. Les bases SQLite de développement
créent leurs tables au démarrage.

## Validation

Tests unitaires et HTTP isolés pour droits, expiration, usage unique, reprise hors ligne,
CLI et conservation des comptes. Smoke tests Mac et terminal avec audio synthétique,
sans téléchargement réel. Le paquet Mac local est signé ad hoc ; la notarisation Apple
reste un processus de distribution distinct, documenté dans `macos.md`.
