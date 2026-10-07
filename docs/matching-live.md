# Suivi web des appariements

Lancer depuis la racine du depot :

```sh
python3 matching_web.py
```

Ouvrir http://127.0.0.1:8094 sur la meme machine. Pour acceder depuis un autre
ordinateur du reseau Tailscale :

```sh
python3 matching_web.py --host 100.104.11.94 --port 8094
```

Ce serveur est reserve au reseau prive de confiance : pas de connexion utilisateur,
pas de publication sur Internet. Il refuse les interfaces publiques et les requetes
API d'autres origines. Docker doit etre accessible a l'utilisateur du serveur.

Le dernier `data/playlist-tests/*/inputs.json` est charge au demarrage.
`--input /chemin/inputs.json` permet de choisir explicitement une execution.
Les metadonnees sources ne sont pas relues sur les plateformes.

- Cocher les scenarios, puis **Lancer**. Une seule execution simultanee est autorisee.
- Selectionner un scenario a gauche pour voir ses titres, meme avant le lancement.
- Le compteur indique les titres definitivement traites, pas une estimation du temps.
- Une erreur sur une plateforme n'empeche pas les tentatives sur les suivantes.
- Les journaux, filtres et liens sont actualises pendant la recherche.
- **Arreter** interrompt le script et supprime uniquement ses conteneurs Docker.
- Si le nettoyage echoue, le lancement reste bloque : verifier Docker et les
  conteneurs portant le label `tunesaver.matching-job`, puis redemarrer le serveur.

La commande lancee est `python3 run_matching_tests.py --docker --events`, avec
les chemins et les scenarios selectionnes. Aucun fichier audio n'est telecharge.
L'ordre est TIDAL, Beatport, puis Qobuz ; les cibles natives evitent la recherche.

Les scenarios termines sont sauvegardes dans `matching-live/` a cote de l'entree.
Le rapport HTML et le JSON deviennent disponibles apres le premier scenario termine.
Un arret conserve ces fichiers, mais pas les resultats partiels du scenario courant.
Recharger la page retrouve l'execution active ; redemarrer le serveur ne la reprend pas.
Fermer un onglet ne stoppe pas le traitement. Utiliser **Arreter** ou Ctrl+C sur le serveur.
