# Dig

Dig est un espace de decouverte personnel, accessible dans la navigation de la
bibliotheque. Il ne telecharge aucun son sans action explicite de l'utilisateur.

## Utilisation

Creer une recherche nommee puis ajouter des references : sons, playlists, tags ou
dossiers. Les references sont resolues exclusivement dans le compte connecte.
Les collections vides ou les sons sans artiste ne constituent pas des references
exploitables. Les doublons sont retires, et jusqu'a 12 morceaux representatifs
sont retenus avec une priorite aux choix individuels et une diversite d'artistes.
La liste exacte des morceaux retenus est consultable dans la recherche.

Le mode Proche favorise la proximite des recommandations. Exploration augmente
le poids des artistes absents des references. Ce n'est pas une analyse sonore,
ni une mesure de compatibilite harmonique : aucun pourcentage de compatibilite
ou tempo manquant n'est invente.

Les suggestions sont affichees par lots de 20, jusqu'a 60 par recherche, et les
sources de telechargement sont verifiees progressivement. Les pochettes,
albums, labels, tempos et tonalites sont affiches lorsqu'ils sont disponibles.
Les raisons et les morceaux de reference restent accessibles pour chaque titre.

- Garder : favori persistant, avec un leger bonus d'artiste lors des prochaines recherches.
- Ignorer : exclusion du titre dans les futures recherches ; retablir depuis la vue Ignores.
- Creuser : nouvelle recherche a partir d'une suggestion, sans devoir la telecharger.
- Telecharger : ajoute le son a Mes sons, ou a la playlist choisie. Les jobs existants
  sont reutilises ; les morceaux deja disponibles ne sont pas telecharges a nouveau.
- Les logos ouvrent la plateforme correspondante. La lecture interne est disponible
  une fois le fichier ajoute et pret dans la bibliotheque. Pas de faux bouton d'extrait.

Les recherches peuvent etre renommees, supprimees, arretees ou relancees.
Modifier les references prepare une nouvelle recherche sans ecraser la precedente.
La suppression d'une recherche ne supprime ni playlists, ni fichiers, ni preferences.

## Sources

ListenBrainz fonctionne sans cle : correspondance exacte artiste/titre via
`acr-lookup/json`, puis `similar-recordings/json`. L'identite exacte est exigee pour
ne pas recommander a partir d'un mauvais enregistrement. Les remixes restent distincts.
La couverture depend du catalogue et des donnees d'ecoute de ListenBrainz.

Last.fm est facultatif : renseigner `TUNESAVER_LASTFM_API_KEY` dans `.env` puis
recreer le conteneur web. La methode `track.getSimilar` enrichit les candidats.
Sans recommandations, le moteur cherche d'autres sorties du meme artiste sur
les catalogues actifs, en indiquant explicitement cette origine moins precise.

Les candidats sont recherches sur TIDAL, Beatport puis Qobuz jusqu'a une
correspondance fiable. Ne pas afficher une source signifie qu'elle n'est pas
verifiee, pas necessairement que le titre est absent de son catalogue.
Spotify et SoundCloud ne sont pas utilises comme moteurs de recommandations.
L'analyse locale audio et les extraits avant telechargement ne font pas partie
de cette version.

## Exploitation

Etat, references, resultats et preferences sont stockes dans SQLite. Un seul
worker Dig s'execute, avec les semaphores de recherche existants. Les sessions
interrompues reprennent apres redemarrage et les resultats deja verifies restent
enregistres. Deux recherches actives et 100 recherches sauvegardees par compte.
Les verifications sont bornees dans le temps et peuvent etre relancees.

Le cache de metadonnees dure 24 heures. Les appels ListenBrainz/Last.fm sont
espaces d'au moins une seconde et ont des timeouts reseau. Seuls les noms des
artistes et titres sont envoyes aux services externes : pas de fichiers audio,
adresses email, noms de dossiers ou noms de tags. Les couvertures distantes sont
chargees par le navigateur, sans referent.

Sources documentaires :
- https://labs.api.listenbrainz.org/acr-lookup
- https://labs.api.listenbrainz.org/similar-recordings
- https://www.last.fm/api/show/track.getSimilar

Tests : `python -m unittest discover -s tests`.
