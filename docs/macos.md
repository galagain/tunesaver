# TuneSaver pour Mac — 0.1.0

L'application ouvre directement une bibliothèque personnelle. Aucun compte
TuneSaver, administrateur, service SMTP, PostgreSQL ou Docker n'est nécessaire.
Les recherches et les imports contactent les plateformes depuis le Mac de
l'utilisateur. Les fichiers déjà importés peuvent être écoutés hors ligne.

Le paquet construit le 28 octobre 2026 cible **Apple Silicon, macOS 26 ou plus**.
Les bibliothèques audio Homebrew utilisées exigent cette version. Le script
calcule la version minimale à partir des bibliothèques embarquées. Pour prendre
en charge des Mac plus anciens, reconstruire et tester les dépendances sur une
version antérieure de macOS. La compilation Intel n'a pas été validée.

## Installation et comptes

Ouvrir `dist/TuneSaver-0.1.0-macOS-arm64.dmg`, puis glisser TuneSaver dans
Applications. Une archive ZIP et l'application `.app` sont également produites.
Le paquet de développement est signé localement, **sans notarisation Apple**.
Il sert aux essais locaux ; ce n'est pas encore le paquet destiné à une diffusion
publique sans avertissement Gatekeeper.

Dans **Mes plateformes** :

- TIDAL : démarrer la connexion, puis ouvrir le lien d'autorisation TIDAL et
  autoriser son compte dans le navigateur. TuneSaver attend la confirmation.
- Beatport : saisir son propre identifiant et son mot de passe. La connexion
  est vérifiée avant de mémoriser les données et de démarrer le moteur local.
- Qobuz : une fenêtre ouvre `https://play.qobuz.com`. Après connexion sur ce
  site, l'application récupère la session de cette fenêtre. Une saisie de
  l'identifiant et du jeton reste disponible si le site change son format.

Les abonnements et droits d'accès nécessaires restent ceux des plateformes.
Spotify, Apple Music et SoundCloud restent des sources de métadonnées pour les
imports existants ; cette version n'ajoute pas d'accès à leurs comptes privés.

**Limite Qobuz :** le connecteur déjà présent dans le projet indique que le
changement d'API d'avril 2026 affecte ses téléchargements. La connexion et la
recherche sont prises en charge, mais le téléchargement Qobuz ne doit pas être
présenté comme validé. Cette limite est affichée dans l'application.

## Données locales

`~/Library/Application Support/TuneSaver/` contient :

- `library.sqlite` : titres, playlists, tags et historique ;
- `Music/` : morceaux importés et archives, sans expiration automatique ;
- `queue/` : tâches et journaux persistants ;
- `logs/` : diagnostics de l'application et des moteurs ;
- `session/` : fichiers temporaires de connexion, privés, supprimés à la
  fermeture et nettoyés au lancement suivant après un arrêt brutal.

Les connexions persistantes sont dans le Trousseau macOS, sous le service
`com.tunesaver.desktop.platforms`. Les outils tiers lisent des copies temporaires
accessibles uniquement à l'utilisateur courant (répertoires 0700, fichiers 0600).
Les mots de passe et jetons ne sont pas enregistrés dans le stockage web ni dans
la base de bibliothèque. **Déconnecter ce compte** arrête le moteur concerné et
supprime sa connexion locale et son entrée du Trousseau ; les morceaux restent.
Cela ne révoque pas toutes les sessions du compte sur la plateforme.

La fenêtre utilise un moteur HTTP intégré sur `127.0.0.1`, sur un port aléatoire.
Ce moteur tourne exclusivement sur le Mac, ferme avec l'application et exige un
cookie HttpOnly privé à chaque lancement. Les autres sites et les postes du
réseau n'ont pas accès à la bibliothèque. Un lien de démarrage à usage unique
établit ce cookie. Les routes de création de comptes et d'administration sont
désactivées. Les réglages du serveur ne sont jamais hérités.

Fermer TuneSaver interrompt ses téléchargements en cours. La file et l'historique
sont conservés ; un import interrompu peut devoir être relancé. Le Mac doit rester
éveillé et l'application ouverte pour effectuer des imports.

## Construire

Sur le Mac de construction uniquement : Xcode/Command Line Tools, Homebrew et uv.

```sh
brew install uv go cmake pkg-config taglib fftw libsndfile ffmpeg
python3 desktop/build_macos.py
```

Le script vérifie les empreintes des sources tierces, construit les moteurs,
embarque Python portable 3.13.15, les dépendances Python verrouillées, FFmpeg,
les bibliothèques natives et Chromium headless pour les imports Spotify publics.
Il construit la fenêtre Swift/AppKit/WKWebView, signe le paquet pour les essais
locaux et génère les archives, le DMG, `build-info.json` et `SHA256SUMS`.
L'utilisateur final n'installe aucun de ces outils.

`--skip-native-build` réutilise les outils présents dans `.build/bin`.
`--skip-archives` produit seulement l'application pour accélérer les essais.
Ne pas copier `.env`, `data`, `secrets` ou des fichiers de session dans le paquet.
Les notices tierces et les provenances sont dans `Contents/Resources/Licenses`.
Conserver les sources correspondantes et notices des composants distribués avec
chaque version, notamment les composants GPL.

## Vérifications

```sh
uv venv --python 3.13.15
uv pip install --python .venv/bin/python -r desktop/requirements.lock
PATH="$PWD/.build/bin:$PATH" .venv/bin/python -m unittest discover -s tests
.venv/bin/python tests/desktop_smoke.py --resources dist/TuneSaver.app/Contents/Resources
```

Les tests couvrent les sessions locales, les refus d'accès intersite, les routes
désactivées, les connexions simulées, les erreurs du Trousseau, la persistance et
le nettoyage. L'essai navigateur utilise le Python réellement embarqué, un
Trousseau simulé et un son synthétique : création de playlist, lecture, export,
conservation d'un vieux fichier, redémarrage et absence d'erreurs JavaScript.
La fenêtre WKWebView a aussi été ouverte et vérifiée sur ce Mac.

Les connexions et téléchargements avec de vrais abonnements personnels restent
à valider dans l'application. Aucun compte du serveur n'a été utilisé pour ces
essais. Les changements n'exigent pas l'arrêt du serveur existant.

## Diffusion publique

Il manque un certificat **Developer ID Application** et un profil de notarisation
Apple sur ce Mac. Un certificat **Apple Development** ne remplace pas Developer ID
pour cette distribution. Voir la
[documentation de notarisation Apple](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).

Après installation du certificat avec sa clé privée et configuration d'un profil
`notarytool` dans le Trousseau :

```sh
python3 desktop/release_macos.py \
  --identity 'Developer ID Application: NOM (TEAMID)' \
  --keychain-profile tunesaver-notary
```

Ce script travaille sur une copie dans `dist/release`, active le hardened runtime,
signe les composants, soumet le ZIP à Apple, vérifie l'acceptation, agrafe le ticket
et vérifie Gatekeeper. Il ne publie rien. Valider de nouveau lecture, analyse,
imports et connexions sur ce paquet signé avant de l'héberger pour les utilisateurs.
Les chemins de notarisation ne peuvent pas être testés sans le certificat requis.

Le code Mac est basé sur le commit serveur
`fa62c1d0b5f4c43bf253b0a9688266a25c6edb37` ; les données et comptes de cette
installation ne font pas partie de la version Mac.
