# TuneSaver

Cette stack lance une interface web locale et trois workers indépendants et persistants :
un pour `tiddl`, un pour `BeatportDL` et un pour `qobuz-dl`. Une URL ajoutée depuis le navigateur ou avec
`./musicdl add ...` devient une tâche durable. Les tâches passent par `pending`,
`running`, puis `done` ou `failed`; après un redémarrage, une tâche restée dans
`running` est automatiquement remise dans la file.

Le projet n'embarque ni mot de passe ni jeton dans l'image. Les téléchargements,
l'authentification et la file sont montés depuis la machine hôte.

## Prérequis

- macOS, Linux ou WSL2, avec Bash et les outils Unix usuels (`id`, `chmod`,
  `mktemp`).
- Docker Desktop, ou Docker Engine avec `docker compose` v2. Pour que les workers
  repartent après un redémarrage, Docker doit lui-même démarrer avec la machine.
- La CLI Supabase pour appliquer la migration d'authentification et d'historique.
- Une connexion Internet lors du premier build (sources et paquets sont téléchargés).
- Un compte et un abonnement adaptés aux services utilisés.
- Le droit de télécharger et conserver le contenu concerné. Respectez les conditions
  de TIDAL/Beatport/Qobuz et la législation locale; ne redistribuez pas les fichiers.

[BeatportDL](https://github.com/unspok3n/beatportdl/blob/main/README.md) indique
nécessiter un abonnement Beatport Streaming actif. La qualité `lossless` configurée
ici nécessite l'offre Professional; choisissez une qualité compatible dans
`config/beatport.json` si votre offre est différente. [`tiddl`](https://github.com/oskvr37/tiddl)
est un outil tiers non affilié à TIDAL et son auteur le présente pour un usage
personnel.
[`qobuz-dl`](https://github.com/vitiko98/qobuz-dl) nécessite un abonnement Qobuz
actif pour télécharger. L'intégration utilise directement son code source épinglé.

## Première installation

```sh
cd tunesaver
supabase login
supabase link --project-ref VOTRE_PROJECT_REF
supabase db push --dry-run
supabase db push
./musicdl init
./musicdl auth tiddl
./musicdl auth qobuz
./musicdl start
```

`supabase link` demande le mot de passe PostgreSQL courant. Ne le placez jamais dans
Git, `.env`, une commande copiée dans l'historique du shell ou une conversation. La
migration crée `profiles` et `download_history`, active RLS et limite chaque ligne à
son propriétaire Supabase.

`./musicdl init` demande aussi l'URL et la clé **publishable** visibles dans le bouton
**Connect** du projet Supabase. Pour une installation automatisée, fournissez-les avec
`SUPABASE_URL` et `SUPABASE_PUBLISHABLE_KEY` lors du premier `init`.

## Autoriser un utilisateur

Un visiteur crée son compte depuis la page, confirme son e-mail, puis reste bloqué avec
le message « compte en attente d'autorisation ». Dans Supabase, ouvrir **Table Editor →
profiles** et passer `approved` à `true` uniquement pour les personnes autorisées.

La clé `SUPABASE_PUBLISHABLE_KEY` est publique par conception et peut être utilisée par
le navigateur. N'ajoutez jamais une clé `service_role` au frontend.

`auth` construit l'image au premier lancement et `start` réutilise ensuite le cache
de build. `start` attend également les healthchecks, afin de rendre visible un
conteneur qui ne démarre pas correctement. La commande `./musicdl build` reste
disponible pour préconstruire l'image.

`init` demande les identifiants Beatport et le jeton de session Qobuz sans afficher les
valeurs sensibles. Ils sont enregistrés dans `secrets/` avec les permissions `0600` et ce dossier est exclu
du contexte Docker et de Git. L'authentification TIDAL affiche une URL à ouvrir dans le
navigateur de la machine hôte; son jeton est ensuite conservé sous `data/state/tiddl/`.
Qobuz ayant désactivé la connexion e-mail/mot de passe pour les outils tiers, `auth qobuz`
demande le champ `id` et le champ `token` (appelé `user_auth_token` dans certaines
versions) de la session `play.qobuz.com`, puis valide
l'abonnement et crée la configuration privée sous
`data/state/qobuz/.config/qobuz-dl/`.

Pour récupérer ces deux valeurs, se connecter sur <https://play.qobuz.com>, ouvrir les
outils développeur du navigateur, onglet **Console**, puis exécuter :

```js
JSON.parse(localStorage.getItem('localuser'))
```

Ne partagez jamais le résultat complet : le champ `token` donne accès à la session.

## Changer de compte

Arrêter d'abord les services pour éviter qu'un téléchargement utilise l'ancien compte :

```sh
./musicdl stop
```

Puis exécuter uniquement la commande de la plateforme concernée :

```sh
# TIDAL : une nouvelle URL de connexion sera affichée
./musicdl auth tiddl --force

# Beatport : le nouvel identifiant et le nouveau mot de passe seront demandés
./musicdl init --force-beatport --skip-qobuz

# Qobuz : le nouvel id et le nouveau token seront demandés
./musicdl auth qobuz --force
```

Redémarrer ensuite la stack et vérifier son état :

```sh
./musicdl start
./musicdl doctor
```

Changer de compte ne supprime ni les téléchargements ni l'historique des tâches. Les
nouveaux secrets restent uniquement dans `secrets/` et `data/state/`, jamais dans Git.

Pour n'utiliser que TIDAL :

```sh
./musicdl init --skip-beatport --skip-qobuz
./musicdl auth tiddl
./musicdl start tiddl
```

## Interface web

Après `./musicdl start`, ouvrir :

```text
http://127.0.0.1:8080
```

Si `WEB_PORT` est modifié dans `.env`, remplacer `8080` par cette valeur.

Le champ accepte :

- un nom de morceau ou d'artiste, recherché en parallèle sur TIDAL, Beatport et Qobuz;
- un lien direct TIDAL, Beatport ou Qobuz;
- une liste de 50 liens maximum, avec un lien par ligne;
- une ressource courte tiddl comme `track/123456`.

Une recherche par nom propose jusqu'à huit morceaux par plateforme. Le bouton
**Télécharger** ajoute le résultat à la file; le worker correspondant s'en occupe en
arrière-plan et l'état apparaît en bas de la page. Pour télécharger un album, une
playlist ou une release complète, coller son lien direct : l'interface affiche un
libellé explicite et demande confirmation avant de l'ajouter. Les liens d'artiste ou de
label, susceptibles de lancer un téléchargement massif, sont volontairement refusés
par l'interface web. Une fois prêt, le bouton **Récupérer** envoie le fichier au
navigateur avec un lien temporaire. Après un transfert complet, sa copie temporaire est
supprimée du serveur; l'historique Supabase reste disponible.

Chaque historique, file visible et dossier temporaire est séparé par l'identifiant
Supabase. Le montant affiché est une **estimation**, pas une économie comptable : par
défaut 1,29 € par piste TIDAL et 1,49 € par piste Beatport ou Qobuz. Ces valeurs peuvent
être adaptées avec `MUSICDL_TIDDL_TRACK_VALUE_CENTS`,
`MUSICDL_BEATPORT_TRACK_VALUE_CENTS` et `MUSICDL_QOBUZ_TRACK_VALUE_CENTS`.

Avec `./musicdl start tiddl`, `beatport` ou `qobuz`, les autres workers restent arrêtés;
ses boutons de téléchargement sont désactivés et le serveur refuse de créer une tâche
qui resterait sans worker.

La première recherche Beatport peut prendre un peu plus de temps pendant la création du
cache de connexion. Si une plateforme doit rafraîchir son jeton alors qu'un téléchargement
est actif, l'interface demande simplement de réessayer après celui-ci.

Le port Docker reste publié uniquement sur `127.0.0.1`. Pour un accès Internet, placez
TuneSaver derrière un reverse proxy HTTPS et ajoutez exactement le domaine dans
`TUNESAVER_ALLOWED_HOSTS` (sans `https://`). N'exposez pas directement le port 8080 et
n'utilisez pas `*` comme hôte autorisé.

## Utilisation quotidienne

Ajouter des URL :

```sh
./musicdl add tiddl 'https://tidal.com/album/...'
./musicdl add beatport 'https://www.beatport.com/release/.../...'
./musicdl add qobuz 'https://play.qobuz.com/album/...'
```

Ajouter tout un fichier (lignes vides et commentaires `#` ignorés) :

```sh
./musicdl add-file beatport ../beatport-links.txt
```

Suivre et administrer la file :

```sh
./musicdl status
./musicdl status beatport --limit 50
./musicdl logs
./musicdl retry all
./musicdl retry beatport 20260827T120000Z-ab12cd34
```

Exécuter une URL immédiatement sans créer de tâche :

```sh
./musicdl run tiddl 'https://tidal.com/track/...'
```

Un verrou par service empêche cette commande, une réauthentification et le worker
d'écrire simultanément dans les mêmes jetons, caches ou fichiers.

Arrêter les conteneurs ne supprime rien :

```sh
./musicdl stop
```

## Emplacements persistants

Par défaut :

- TIDAL : `data/downloads/tiddl/`
- Beatport : `data/downloads/beatport/`
- Qobuz : `data/downloads/qobuz/`
- File et journaux détaillés : `data/queue/`
- Fichiers web en attente de récupération : `data/deliveries/`
- Jetons/cache : `data/state/`
- Secrets Beatport et Qobuz : `secrets/`

Les historiques `done`/`failed` et les journaux détaillés ne sont pas supprimés
automatiquement. Sur une machine utilisée longtemps, archivez ou nettoyez
occasionnellement `data/queue/logs/` et les anciens fichiers JSON, conteneurs arrêtés.

Pour réutiliser les dossiers déjà présents dans ce dépôt, modifier `.env` après
`./musicdl init` :

```dotenv
TIDDL_DOWNLOADS_PATH=../tiddl/downloads
BEATPORT_DOWNLOADS_PATH=../downloads
QOBUZ_DOWNLOADS_PATH=../qobuz-downloads
```

Un chemin absolu est préférable si la stack doit écrire sur un disque externe ou un
NAS. Les chemins doivent exister et être accessibles à `PUID`/`PGID`. Avec Docker
Desktop, un dossier situé hors du projet doit aussi être autorisé dans le partage de
fichiers de Docker.

## Configuration

- `config/tiddl.toml` règle la qualité, les métadonnées et le nombre de threads TIDAL.
- `config/beatport.json` contient uniquement les réglages non secrets de BeatportDL.
- `config/qobuz.ini` contient uniquement les réglages non secrets de qobuz-dl.
- `.env` règle l'utilisateur du conteneur, le polling, `WEB_PORT` et les dossiers de
  sortie hôte.

Les valeurs par défaut sont `high` pour TIDAL, `lossless` pour Beatport et la qualité
Qobuz `27` avec repli automatique. Les fichiers déjà présents sont ignorés ou mis à jour selon
les options propres aux outils.

## Publier le projet sur GitHub

GitHub conserve le code, mais n'exécute pas cette application Docker. GitHub Pages ne
convient pas car l'interface a besoin du serveur Python et des workers. Le dépôt sert à
installer facilement le projet sur une autre machine équipée de Docker.

Créer d'abord un dépôt **privé et vide** sur GitHub, sans README généré, puis exécuter
depuis ce dossier :

```sh
git init
git add .
git status
git commit -m "Initial version"
git branch -M main
git remote add origin https://github.com/VOTRE_COMPTE/tunesaver.git
git push -u origin main
```

Avant le commit, `git status` ne doit jamais afficher `.env`, les téléchargements,
les états de connexion ou les vrais fichiers de secrets. Seuls `data/README.md` et
`secrets/README.md` sont volontairement versionnés dans ces dossiers. Ne forcez jamais
l'ajout d'un fichier sensible avec `git add -f`.

## Déployer depuis GitHub sur une autre machine

Après avoir installé Docker et Git :

```sh
git clone https://github.com/VOTRE_COMPTE/tunesaver.git
cd tunesaver
supabase login
supabase link --project-ref VOTRE_PROJECT_REF
supabase db push
./musicdl init
./musicdl auth tiddl
./musicdl auth qobuz
./musicdl start
```

Ouvrir ensuite <http://127.0.0.1:8080>. Chaque machine construit une image native pour
son architecture (`amd64` ou `arm64`) et possède ses propres comptes. `./musicdl init`
doit être utilisé avant un éventuel `docker compose up` direct : il crée les dossiers,
les secrets et les permissions nécessaires.

Pour mettre à jour une machine déjà installée :

```sh
git pull --ff-only
./musicdl build
./musicdl start
```

### Déplacer aussi les téléchargements et l'historique

GitHub ne transporte volontairement pas ces données. Pour les migrer séparément :

1. lancer `./musicdl stop` sur la machine source;
2. cloner le dépôt GitHub sur la nouvelle machine;
3. copier `data/` par un canal privé (`rsync`, disque externe ou sauvegarde chiffrée);
4. si `.env` pointe hors de `data/`, copier séparément ces dossiers de téléchargement;
5. copier `.env` et `secrets/` uniquement par un canal sûr, ou lancer `./musicdl init`
   sur la cible pour les recréer;
6. adapter `PUID`, `PGID` et les chemins dans `.env`, puis rendre les dossiers copiés
   accessibles à cet utilisateur;
7. si les états d'authentification n'ont pas été copiés, relancer `./musicdl auth tiddl`
   et `./musicdl auth qobuz`;
8. lancer `./musicdl doctor`, puis `./musicdl start`.

Le fichier `data/state/tiddl/auth.json`, le cache Beatport et la configuration Qobuz sont sensibles. Il est plus
sûr de se reconnecter sur la nouvelle machine que de les envoyer par un canal non sûr.
`doctor` vérifie la présence et la structure locale des fichiers nécessaires; la
validité réseau des comptes est contrôlée par les outils lors du prochain téléchargement.
Les workers partagent uniquement la file nécessaire aux déplacements atomiques des
tâches. Aucun ne reçoit le jeton, le cache ni le dossier de téléchargement de l'autre.
Le service web reçoit les trois authentifications uniquement pour effectuer les
recherches, mais aucun montage vers les téléchargements; il reste accessible seulement
depuis la machine locale.

## Versions épinglées

- `tiddl` et ses dépendances Python sont épinglés à des versions précises, dont
  `tiddl==3.4.4`.
- BeatportDL est construit depuis le commit
  `878d3f61c5a98cffd43b9e0306b8cbc75488999e`; l'archive est vérifiée par SHA-256.
- qobuz-dl utilise le correctif d'authentification par jeton proposé en amont, épinglé au
  commit `00f8ce21b276ddf9278f0a944a2f85de2421d98d`; l'archive est vérifiée par SHA-256.
- Les images de base sont référencées par les tags Python `3.13.15` et Go `1.26.7`,
  Debian Trixie.

Ces versions facilitent un redéploiement cohérent, sans constituer un build bit à bit :
les images ne sont pas épinglées par digest, les paquets Debian suivent leurs dépôts et
les dépendances Python n'ont pas de hashes dans `requirements.lock`.

Les sources BeatportDL/qobuz-dl et leurs licences sont conservées via leurs commits
publics; une copie de chaque licence est incluse dans l'image finale.
