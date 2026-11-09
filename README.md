<p align="center">
  <img src="web/tunesaver.svg" alt="Logo TuneSaver" width="112" height="112">
</p>

<h1 align="center">TuneSaver</h1>

<p align="center">
  Une application Mac, une commande terminal et une interface web<br>
  pour télécharger, écouter et organiser sa musique.
</p>

<p align="center">
  <a href="https://github.com/galagain/tunesaver/releases/tag/v0.4.0"><img src="https://img.shields.io/badge/version-0.4.0-67e5b0?style=flat-square&amp;labelColor=1b1d1c" alt="Version 0.4.0"></a>
  <img src="https://img.shields.io/badge/macOS-Apple_Silicon-eee?style=flat-square&amp;labelColor=1b1d1c" alt="macOS Apple Silicon">
  <img src="https://img.shields.io/badge/serveur-Docker-eee?style=flat-square&amp;labelColor=1b1d1c" alt="Serveur Docker">
</p>

<p align="center">
  <a href="https://github.com/galagain/tunesaver/releases/tag/v0.4.0"><strong>Télécharger</strong></a> ·
  <a href="https://tunesaver.galagain.com">Ouvrir le web</a> ·
  <a href="#démarrer">Installation</a> ·
  <a href="#documentation">Documentation</a>
</p>

---

## Une bibliothèque, plusieurs façons de l’utiliser

- **Télécharger** depuis TIDAL, Beatport et Qobuz avec ses comptes connectés.
- **Importer des playlists** et retrouver leurs titres sur les plateformes disponibles.
- **Écouter et organiser** ses morceaux dans une bibliothèque avec playlists et tags.
- **Connecter un appareil depuis le web**, sans recopier les fichiers des comptes.
- **Synchroniser le catalogue** : titres, artistes, plateformes et liens. Les audios restent sur leur machine.

## Démarrer

| Vous souhaitez… | Choisissez… | Première étape |
| --- | --- | --- |
| Une application sur votre Mac | **Mac** | Installer l’application, puis ouvrir **Mes plateformes** |
| Télécharger depuis le terminal | **CLI Mac** | Installer la commande depuis le menu TuneSaver |
| Utiliser le service en ligne | **Web** | [Ouvrir TuneSaver](https://tunesaver.galagain.com) avec un compte approuvé |
| Héberger votre propre instance | **Docker** | Lancer `./tunesaver server setup` dans le dépôt |

### Mac

1. Télécharger le [DMG 0.4.0](https://github.com/galagain/tunesaver/releases/download/v0.4.0/TuneSaver-0.4.0-macOS-arm64.dmg).
2. Glisser **TuneSaver** dans **Applications**.
3. Ouvrir **Mes plateformes → Se connecter avec TuneSaver**.

Le navigateur reconnaît votre session TuneSaver. Confirmez le code affiché et les
plateformes à connecter. La connexion manuelle reste disponible.

> Le paquet actuel cible **Apple Silicon et macOS 26 ou plus récent**. Il est signé
> localement, mais **pas encore notarié par Apple** ; macOS peut bloquer son ouverture.
> Voir le [guide Mac](docs/macos.md) pour les détails de distribution.

### Terminal

Dans l’application : **TuneSaver → Installer la commande tunesaver…**, puis ouvrir
un nouveau terminal. Python et les outils audio sont inclus ; aucun Docker ni
Homebrew n’est nécessaire pour utiliser le client Mac.

```sh
tunesaver login
tunesaver "https://tidal.com/track/123"
```

Le Mac et le terminal partagent leurs comptes dans le Trousseau. Si vous êtes déjà
connecté dans l’application, passez directement au téléchargement.

```sh
tunesaver                            # Recherche interactive
tunesaver search "artiste titre"      # Rechercher un morceau
tunesaver status                     # Vérifier les comptes
tunesaver sync                       # Synchroniser les métadonnées
```

Les téléchargements vont dans `~/Music/TuneSaver`. Pour choisir un dossier :

```sh
tunesaver download "https://tidal.com/track/123" -o "$HOME/Music/Mon set"
```

<details>
<summary><strong>Installer uniquement le terminal</strong></summary>

Télécharger l’[archive CLI](https://github.com/galagain/tunesaver/releases/download/v0.4.0/TuneSaver-0.4.0-macOS-arm64-CLI.tar.gz), puis :

```sh
tar -xzf TuneSaver-0.4.0-macOS-arm64-CLI.tar.gz
./TuneSaverCLI/tunesaver install --configure-shell
```

Ouvrir un nouveau terminal, puis lancer `tunesaver login`.
Pour Linux, utiliser le déploiement Docker ci-dessous.

</details>

### Serveur Docker

**Prérequis :** Git, Python 3 et Docker avec Compose v2.

```sh
git clone https://github.com/galagain/tunesaver.git
cd tunesaver
./tunesaver server setup
```

La configuration prépare les dossiers, crée le mot de passe administrateur,
connecte les plateformes et démarre les services disponibles. L’interface est
accessible sur **<http://127.0.0.1:8080>**.

Sur une machine sans navigateur, ouvrez le lien de connexion depuis un autre
appareil et confirmez le code. SQLite fonctionne par défaut ; un
[PostgreSQL autonome](docs/postgres.md) est disponible en option.

```sh
./tunesaver server status
./tunesaver server logs
./tunesaver server stop
```

Pour publier le site, configurer le domaine et un proxy HTTPS. L’inscription et la
récupération par e-mail nécessitent SMTP. Le [guide serveur](docs/server.md)
détaille la configuration, les mises à jour et le stockage.

## Comptes et données

| Élément | Fonctionnement |
| --- | --- |
| Comptes des plateformes | Trousseau sur Mac ; fichiers privés côté serveur |
| Connexion depuis le web | Compte approuvé, confirmation du code et autorisation à usage unique |
| Catalogue global | Métadonnées uniquement : titres, artistes, plateformes et liens |
| Audio | Conservé sur la machine de téléchargement ; aucun transfert par la synchronisation du catalogue |
| Conservation | Le Mac conserve les audios ; le serveur web applique une expiration de sept jours par défaut |

L’approbation d’un utilisateur sur le serveur lui permet de récupérer les
connexions des plateformes proposées par ce serveur. Les abonnements et droits
d’accès des plateformes restent nécessaires.

Les secrets, fichiers audio et bases locales ne sont pas versionnés. Sauvegardez
vos données et configurations avant une mise à jour.

## Documentation

| Guide | Contenu |
| --- | --- |
| [Mac et terminal](docs/macos.md) | Installation, construction et données locales |
| [Serveur Docker](docs/server.md) | Configuration, commandes et exploitation |
| [PostgreSQL](docs/postgres.md) | Base autonome, migration et sauvegardes |
| [Connexion des appareils](docs/account-transfer.md) | Autorisation web et import manuel |
| [Catalogue global](docs/catalog-sync.md) | Synchronisation des métadonnées |
| [Architecture](docs/architecture.md) | Moteur partagé, interfaces et déploiement |
| [Rétention audio](docs/audio-retention.md) | Conservation et nettoyage des fichiers |

---

<p align="center">
  <img src="web/tunesaver.svg" width="28" height="28" alt="Logo TuneSaver"><br>
  <sub>Une seule commande : <code>tunesaver</code>.</sub>
</p>
