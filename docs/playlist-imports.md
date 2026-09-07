# Imports de playlists

Dans **Importer une playlist**, coller un lien complet Spotify, SoundCloud ou
Beatport, puis cliquer sur **Importer et telecharger**. La barre de recherche
accepte aussi ces liens. Les liens courts de partage doivent d'abord etre ouverts
pour obtenir le lien complet.

Une playlist locale est creee. Beatport fournit directement les morceaux ; les
titres Spotify et SoundCloud sont recherches sur TIDAL, Qobuz, puis Beatport.
L'artiste, le titre, la version et la duree connue doivent correspondre. Les
titres non identifies ne sont pas telecharges et figurent dans le bilan d'import.
Il ne s'agit pas d'un telechargement audio depuis Spotify ou SoundCloud.

L'import reste actif apres fermeture du navigateur et reprend apres redemarrage.
Le bilan indique les titres mis en file ; le statut de chaque telechargement
reste visible dans la bibliotheque. Supprimer la playlist arrete son import,
mais ne supprime pas les telechargements deja mis en file. Un nouvel import
reutilise les fichiers deja disponibles pour le meme compte.

## Acces et limites

- Playlists accessibles publiquement pour Spotify et SoundCloud ; les playlists
  Beatport doivent etre accessibles au compte Beatport configure sur le serveur.
- Maximum : 500 titres et trois imports actifs par compte. Les imports sont
  traites successivement pour limiter la charge et les appels aux plateformes.
- L'apercu public Spotify expose au maximum 100 titres. Lorsqu'il atteint cette
  limite, TuneSaver refuse l'import incomplet et demande un acces API Spotify.
- Avec une application Spotify autorisee, configurer dans `.env` les variables
  `TUNESAVER_SPOTIFY_CLIENT_ID`, `TUNESAVER_SPOTIFY_CLIENT_SECRET` et, si un acces
  utilisateur est requis, `TUNESAVER_SPOTIFY_REFRESH_TOKEN`, puis recreer le
  conteneur web. Les permissions et restrictions du mode developpement Spotify
  continuent de s'appliquer. Aucune connexion OAuth Spotify utilisateur n'est
  proposee dans cette version.

Les lecteurs de metadonnees sont dans `src/playlist_sources.py` et le traitement
persistant dans `src/webapp.py`. Les tests utilisent des reponses simulees pour
ne pas lancer de telechargements reels.
