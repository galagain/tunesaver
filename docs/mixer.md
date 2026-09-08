# Mixer / DDJ-FLX4

Le Mixer est un lecteur a deux platines execute dans le navigateur. Il charge
uniquement les fichiers appartenant au compte connecte via les tickets de streaming
existants. Les fichiers audio restent intacts ; analyses, grilles et cues sont
conserves dans SQLite, avec verification du proprietaire a chaque acces.

## Utilisation

- Ouvrir Mixer, brancher le DDJ-FLX4 en USB, puis Connecter le FLX4.
- Accepter la permission MIDI dans Chrome ou Edge sur ordinateur.
- Fermer rekordbox, Serato ou tout autre logiciel utilisant deja le controleur.
- Charger un morceau avec les boutons A/B, par glisser-deposer, ou avec BROWSE/LOAD.
- Appuyer sur BROWSE pour parcourir les playlists et tags. Tourner pour choisir,
  appuyer pour entrer dans la liste des sons, puis LOAD A/B pour charger.
  Shift + BROWSE revient aux collections. Les dossiers sont repliables.
- Les reglages audio permettent de choisir la sortie exposee par le navigateur.
- Le routage quatre canaux est propose uniquement si `maxChannelCount >= 4` :
  master 1/2 et casque 3/4. Verifier physiquement le routage et commencer a faible volume.
  En stereo, la preecoute est desactivee et n'est jamais rabattue dans le master.
- Le debranchement MIDI met la lecture en pause. La fermeture du Mixer met aussi
  les platines en pause, mais conserve leurs morceaux jusqu'a la deconnexion.

## Commandes implementees

- Play/pause, cue (pose a l'arret, retour et pause pendant la lecture).
- Shift + cue revient au debut ; hot cues 1-8, Shift + pad pour les effacer.
- Faders de voie, crossfader a puissance constante, gain, EQ trois bandes, filtre.
- Tempo +/-16 %, maintien de tonalite via `HTMLMediaElement.preservesPitch`.
- Jog en lecture : correction temporaire de vitesse. A l'arret : deplacement fin.
  Shift + jog : recherche rapide. Pas de moteur de scratch vinyle dans cette version.
- Loop In, Loop Out et Reloop/Exit. Les boucles reposent sur les evenements du
  lecteur, pas sur une horloge de moteur DJ : precision non garantie a l'echantillon.
- Headphones Cue A/B et Headphones Mixing si quatre canaux accessibles.
- Browse, Load A/B, voyants play/cue/boucle/hot cues.
- Beat Sync aligne le tempo et la phase du temps le plus proche au moment de
  l'appui. Ce n'est pas une synchronisation continue ni un alignement des phrases.
- Smart CFX, Smart Fader, effets Beat FX, sampler et les autres modes de pads
  ne sont pas pris en charge. Aucune compatibilite avec d'autres modeles n'est annoncee.

Les huit hot cues colores sont visibles dans les courbes detaillees et l'apercu.
La croix de chaque pad (ou Shift + pad) le supprime. Leur position et le cue
principal sont sauvegardes par morceau et par compte, meme apres deconnexion.
Chargement maximal : 128 Mio par platine, verifie aussi sans Content-Length.
Le chargement complet permet de lire sans conserver un ticket actif, mais ne
constitue pas un mode hors ligne persistant. Les formats doivent etre decodables
par le navigateur ; un echec est affiche dans la platine.

## Analyse et grilles

Les nouveaux sons et la bibliotheque existante sont analyses automatiquement en
arriere-plan, un fichier a la fois : BPM, tonalite et notation Camelot, duree,
format, debit, frequence et canaux. Les courbes colorees utilisent trois bandes
de frequences du fichier reel, pas une separation des instruments.

Le BPM et la tonalite sont des estimations. La grille constante suppose du 4/4 ;
le premier temps de mesure n'est pas detecte de facon certaine. Les tempos variables
et rythmes incertains sont signales. Dans Grille et analyse, on peut corriger le
BPM, le diviser/multiplier par deux, placer le premier temps, decaler la grille
de 10 ms et choisir la tonalite. Les corrections sont conservees lors d'une
nouvelle analyse. Quantize cale les nouveaux cues et boucles sur cette grille.

Les deux courbes defilantes ont une tete de lecture commune et une echelle de
temps tenant compte du tempo de chaque platine. Le zoom est commun ; clic pour
chercher, fleches gauche/droite pour avancer ou reculer d'un temps.

Le traitement est isole dans un processus sans secrets dans son environnement,
limite a un thread de calcul, 2 Gio d'espace virtuel et 240 secondes CPU
(300 secondes de delai total). Il est arrete proprement au redemarrage puis repris.
Fichiers de 2 secondes a 60 minutes ; archives contenant un seul fichier audio
acceptees, dans la limite de l'espace temporaire disponible. Les echecs ne
bloquent pas le lecteur : reglages manuels et bouton de nouvelle analyse restent
disponibles. Aucune donnee audio n'est envoyee a une API externe.

## Architecture et dependances

- `web/mixer.js` : platines, graphe Web Audio, routage et connexion Web MIDI.
- `web/flx4.js` : decodeur MIDI sans dependance DOM, teste avec Node.
- `web/mix-waveforms.js` : courbes Canvas, grille et calcul de phase.
- `src/mixer_analysis.py` : API privee, stockage SQLite et file d'analyse.
- `src/analyze_audio.py` : FFmpeg, librosa 0.11.0 (ISC) pour le rythme, et
  keyfinder-cli/libkeyfinder pour la tonalite. Dependances Python epinglees dans
  `requirements-analysis.lock`.
- keyfinder-cli et libkeyfinder sont sous GPL-3.0. Le Dockerfile epingle et verifie
  leurs sources ; archives sources completes dans `/usr/share/keyfinder-sources`
  et licence dans `/usr/share/licenses/keyfinder-cli/LICENSE` dans l'image.
  Conserver ces sources et notices si l'image est redistribuee.
- `web/vendor/wavesurfer/wavesurfer.esm.js` : wavesurfer.js 7.12.11,
  distribution npm officielle, licence BSD-3-Clause incluse.
- `web/vendor/mixer-icons.js` : sous-ensemble de Lucide 0.468.0, compile avec
  esbuild 0.25.0. Licence ISC dans `web/vendor/LUCIDE-LICENSE`.
- Bibliotheques servies localement, sans CDN ni transfert audio a un tiers.
- Nonce CSS unique par reponse HTML pour les styles du Shadow DOM de WaveSurfer.
  La CSP reste restrictive pour les scripts. Les blob URLs sont autorisees pour
  l'audio. Permissions MIDI et speaker-selection limitees a la meme origine.

Sources de la correspondance MIDI (adresses et valeurs uniquement) :

- https://www.pioneerdj.com/en/support/software-information/controller/ddj-flx4/
- https://www.pioneerdj.com/-/media/pioneerdj/software-info/controller/ddj-flx4/ddj-flx4_midi_message_list_e1.pdf
- https://github.com/mixxxdj/mixxx/blob/main/res/controllers/Pioneer-DDJ-FLX4.midi.xml

## Tests

```sh
node --test tests/test_flx4.mjs
python -m unittest discover -s tests
```

`tests/mixer-browser.cjs` necessite `@playwright/test`, Chromium et une instance
locale du site (8081 par defaut, surchargeable avec `MIXER_URL`). Le test ne se
connecte a aucun vrai compte et utilise des fichiers WAV synthetiques en memoire.
`CHROMIUM_PATH` permet de choisir le binaire ; les captures sont placees dans `/work`.
Il verifie lecture simultanee et signal audio non nul, commandes MIDI simulees,
waveforms colorees non vides et mobiles, grille, phase, cues persistants et
supprimables, navigation MIDI dans les playlists, filtres, deconnexion et tailles
1440/390 px. Ces tests ne remplacent pas un essai avec un FLX4 physique, notamment
pour la latence, les jogs, les voyants et la preecoute casque.
