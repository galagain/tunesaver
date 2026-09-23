# Imports de playlists

Dans **Importer une playlist**, coller un lien complet Apple Music, Spotify, SoundCloud ou
Beatport, puis cliquer sur **Importer et telecharger**. La barre de recherche
accepte aussi ces liens. Les liens courts de partage doivent d'abord etre ouverts
pour obtenir le lien complet.

Une playlist locale est creee. Beatport fournit directement les morceaux ; les
titres Apple Music, Spotify et SoundCloud sont recherches sur TIDAL, Qobuz, puis Beatport.
L'artiste, le titre, la version et la duree connue doivent correspondre. Les
titres non identifies ne sont pas telecharges et figurent dans le bilan d'import.
Il ne s'agit pas d'un telechargement audio depuis Apple Music, Spotify ou SoundCloud.

L'import reste actif apres fermeture du navigateur et reprend apres redemarrage.
Le bilan indique les titres mis en file ; le statut de chaque telechargement
reste visible dans la bibliotheque. Supprimer la playlist arrete son import,
mais ne supprime pas les telechargements deja mis en file. Un nouvel import
reutilise les fichiers deja disponibles pour le meme compte.

## Rangement de la bibliotheque

Le menu de parametres d'une playlist propose **Telecharger toute la playlist**.
Le ZIP contient les fichiers disponibles, numerotes dans l'ordre enregistre.
Les sons en cours ou indisponibles sont exclus apres confirmation. Les fichiers
restent sur le serveur. Les ZIP contenant un seul morceau sont ouverts pour
inclure directement ce morceau. Il n'y a pas de limite de taille imposee :
le ZIP64 est envoye progressivement, sans fichier temporaire ni copie complete
en memoire. Une seule archive est transferee a la fois.
**Exporter les liens** reste disponible separement.

Les playlists peuvent etre rangees dans des dossiers a un niveau. Le bouton
dossier a cote de Mes playlists permet d'en creer un. Le deplacement fonctionne
par glisser-deposer sur un dossier. Glisser une playlist sur le titre Mes playlists
ou dans la zone des playlists a la racine permet de la sortir de son dossier.
Seule l'indentation distingue les playlists rangees de celles a la racine.
Chaque dossier peut etre renomme, replie ou supprime ; sa suppression conserve
ses playlists et leurs morceaux. Le rangement et l'etat replie sont enregistres
dans le compte utilisateur. La section Sons sans playlist affiche les morceaux
qui n'appartiennent a aucune playlist, y compris dans les dossiers replies.

## Acces et limites

- Playlists accessibles publiquement pour Apple Music, Spotify et SoundCloud ; les playlists
  Beatport doivent etre accessibles au compte Beatport configure sur le serveur.
- Maximum : 500 titres et trois imports actifs par compte. Les imports sont
  traites successivement pour limiter la charge et les appels aux plateformes.
- Apple Music : les titres, artistes et durees proviennent des donnees JSON de
  la page publique music.apple.com, sans compte ni cle API Apple. Le nombre
  extrait doit correspondre au total annonce ; si Apple ne fournit qu'un
  apercu incomplet, l'import echoue explicitement sans lancer les telechargements.
  Les redirections sont limitees au meme identifiant de playlist chez Apple.
- Pour Spotify, sans identifiants API, TuneSaver utilise l'apercu leger pour moins de 100 titres.
  A partir de 100 titres, ou si cet apercu est inaccessible, il ouvre la page publique dans Chromium,
  sans connexion, et fait defiler la liste pour charger toutes les pages.
  Les titres sont collectes dans leur ordre, puis leur nombre est compare au
  total annonce par Spotify. Une liste incomplete n'est pas importee.
  La limite de 100 titres du lecteur integre ne s'applique plus.
  Le defilement cible la premiere position manquante ; une lecture bloquee est
  rechargee automatiquement dans la limite du delai global.
- Le navigateur est lance uniquement pendant la lecture des metadonnees, avec
  un profil temporaire, sans chargement audio. Le processus et ses enfants sont
  fermes apres chaque lecture, y compris en cas de delai depasse (160 secondes).
  Les imports etant traites successivement, un seul navigateur est necessaire.
- Avec une application Spotify autorisee, configurer dans `.env` les variables
  `TUNESAVER_SPOTIFY_CLIENT_ID`, `TUNESAVER_SPOTIFY_CLIENT_SECRET` et, si un acces
  utilisateur est requis, `TUNESAVER_SPOTIFY_REFRESH_TOKEN`, puis recreer le
  conteneur web. Les permissions et restrictions du mode developpement Spotify
  continuent de s'appliquer. Aucune connexion OAuth Spotify utilisateur n'est
  proposee dans cette version.

Les lecteurs de metadonnees sont dans `src/playlist_sources.py` et le traitement
persistant dans `src/webapp.py`. Les tests utilisent des reponses simulees pour
ne pas lancer de telechargements reels.
