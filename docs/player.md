# Lecteur audio

Le site utilise un lecteur HTML audio unique, fixe en bas de la bibliotheque.
Les fichiers sont lus via les tickets authentifies de /api/stream-ticket.
Le lecteur fournit lecture/pause, position, volume et precedent/suivant.
A la fin d'un son, il passe au prochain son lisible de la collection affichee.
La deconnexion et la suppression du son en cours arretent la lecture.
Une demande de lecture devenue obsolete ne peut pas redemarrer le lecteur.

Le mode deux platines, les commandes DJ et la connexion MIDI ont ete retires.
Aucun moteur Web Audio, AudioWorklet ou module WASM n'est charge.
Les donnees musicales et les anciennes analyses stockees sont conservees ;
la lecture ne lance pas d'analyse audio.

Le test navigateur tests/player-browser.cjs couvre Chrome et Firefox,
les petites et grandes resolutions, les pochettes, les changements de son,
l'annulation et la deconnexion. Les autres tests de bibliotheque couvrent
les playlists, tags, recherches et suppressions.
