# Serveur TuneSaver

## Nouveau serveur Docker

Depuis le dépôt : `./tunesaver server setup`.

La commande utilise Docker Compose et Python 3, prépare les dossiers, propose
le mot de passe administrateur et ouvre la connexion web. Le code affiché doit
être confirmé sur le serveur de comptes TuneSaver. Sur un serveur sans interface
graphique, ouvrir ce lien depuis son ordinateur. Les secrets sont écrits dans des
fichiers locaux en permissions 600 ; les comptes existants sont conservés.

Le démarrage lance le site et seulement les plateformes configurées. Une plateforme
absente n’empêche pas les autres de démarrer. La première construction Docker peut
prendre plusieurs minutes ; aucune image publique préconstruite n’est supposée.

Le compte administrateur est `admin`, avec le mot de passe choisi ou généré dans
`.env`. SMTP est nécessaire pour les codes de validation des comptes utilisateurs.
Les droits sur les plateformes restent soumis à leur abonnement et à leur disponibilité.

## Commandes

| Commande | Action |
| --- | --- |
| `tunesaver server setup` | Configurer, connecter et démarrer |
| `tunesaver server login` | Connecter les plateformes depuis le navigateur |
| `tunesaver server start` | Construire si nécessaire et démarrer |
| `tunesaver server stop` | Arrêter sans supprimer les données |
| `tunesaver server status` | Voir les files de téléchargement |
| `tunesaver server logs` | Suivre les journaux |
| `tunesaver server doctor` | Vérifier les connexions locales |
| `tunesaver server retry all` | Relancer les tâches échouées |

Dans le dépôt, préfixer la commande par `./`.
Pour une mise à jour du code : `git pull --ff-only`, puis `./tunesaver server start`.
Sauvegarder les données et appliquer les migrations PostgreSQL avant le démarrage
si la version en introduit.

## Stockage

Le déploiement standard utilise SQLite et le réseau privé Compose. Il n’a besoin
d’aucun autre projet. `data/`, `secrets/` et `.env` persistent entre les redémarrages.
Le port HTTP n’est publié que sur l’interface locale. Pour l’accès distant,
utiliser un proxy HTTPS et renseigner `PUBLIC_URL` et `TUNESAVER_ALLOWED_HOSTS`.

PostgreSQL : définir `TUNESAVER_DATABASE_ENV` dans `.env`.
Le lanceur ajoute alors `compose.postgres.yaml`. Les identifiants restent dans le
fichier privé indiqué. Ne pas changer une installation PostgreSQL en SQLite en
retirant simplement ces variables : cela sélectionnerait une autre base.

PostgreSQL possède son propre conteneur et volume TuneSaver. Voir [PostgreSQL](postgres.md).

## Raspberry installé comme service natif

Le service natif existant utilise le même routeur `tunesaver` avec son environnement
systemd : `tunesaver login`, `tunesaver status`, `tunesaver sync`. Ses fichiers et
unités systemd ne sont pas déplacés. Pour une nouvelle installation Linux, le
parcours distribué est Docker ; il n’existe pas encore de paquet natif Linux universel.
