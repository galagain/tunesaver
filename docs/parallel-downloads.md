# Téléchargements en parallèle

Ce document décrit le fonctionnement de TuneSaver 0.4.0. Il ne change aucun
réglage et n’introduit aucune commande utilisateur. Il n’existe actuellement
ni limite globale configurable dans l’interface, ni option CLI dédiée.

## Deux niveaux de parallélisme

Une **tâche** est une entrée de la file TuneSaver. Elle peut contenir un lien vers
un morceau, un album ou une playlist. Un **morceau** est un fichier audio traité
par le connecteur de la plateforme. Une tâche peut donc produire plusieurs morceaux.

L’application Mac et les services web/Docker/Raspberry disposent d’un processus
qui traite la file de chaque plateforme connectée :

- TIDAL, Beatport et Qobuz peuvent travailler en même temps.
- Chaque plateforme traite une seule tâche de sa file à la fois.
- À l’intérieur de cette tâche, le connecteur peut télécharger plusieurs morceaux
  en parallèle, si la ressource et le connecteur le permettent.

Les trois processus correspondent aux trois plateformes, pas à trois processus
par plateforme. Les limites internes ne multiplient pas le nombre de tâches
TuneSaver exécutées simultanément.

## Réglages actuels

Les valeurs suivantes sont celles des fichiers de configuration du dépôt.

| Plateforme | Fichier source | Paramètre | Valeur | Portée |
| --- | --- | --- | --- | --- |
| TIDAL | [`config/tiddl.toml`](../config/tiddl.toml) | `[download].threads_count` | `4` | Limite du traitement simultané des morceaux dans le connecteur |
| Beatport | [`config/beatport.json`](../config/beatport.json) | `max_download_workers` | `4` | Limite interne des téléchargements ; certaines pochettes utilisent aussi ces places |
| Beatport | [`config/beatport.json`](../config/beatport.json) | `max_global_workers` | `4` | Limite des opérations internes du connecteur, pas un plafond global TuneSaver |
| Qobuz | [`config/qobuz.ini`](../config/qobuz.ini) | Aucun paramètre de parallélisme exposé | Séquentiel | Les morceaux sont traités successivement par le connecteur actuel |

Les deux valeurs Beatport ne s’additionnent pas : `4` et `4` ne signifient pas
huit téléchargements simultanés. `default_limit` dans la configuration Qobuz
n’est pas un nombre de téléchargements parallèles.

Ces limites sont des plafonds. La disponibilité des morceaux, les réponses des
plateformes, les pochettes et le traitement audio peuvent réduire le nombre de
transferts réellement actifs.

## Exemples

**Quarante tâches TIDAL contenant chacune un morceau :** TuneSaver traite les
tâches l’une après l’autre. `threads_count = 4` ne fait pas démarrer quatre tâches
individuelles à la fois.

**Une tâche TIDAL contenant un album :** le connecteur peut traiter jusqu’à quatre
morceaux simultanément dans cette tâche. Les autres tâches TIDAL attendent.

**Une tâche TIDAL et une tâche Beatport :** leurs processus sont indépendants et
peuvent avancer en parallèle. Il n’existe pas de compteur partagé imposant un
maximum de quatre morceaux pour l’ensemble des plateformes.

Une playlist importée puis répartie en tâches individuelles reste soumise à la
première règle ; elle ne bénéficie pas automatiquement du parallélisme interne
d’un téléchargement d’album ou de playlist en une seule tâche.

## Particularité du terminal Mac

Une invocation du terminal traite les liens fournis successivement, y compris
lorsqu’ils concernent des plateformes différentes. Elle ne lance pas les trois
processus de fond de l’application. Le parallélisme interne du connecteur reste
possible pour un lien contenant plusieurs morceaux.

Le terminal et l’application partagent des verrous pour une même plateforme.
Le terminal signale une plateforme occupée plutôt que de lancer une opération
concurrente dessus. Lancer plusieurs terminaux ne contourne donc pas la limite
de cette plateforme sur la même installation.

## Où la configuration est utilisée

- **Docker :** le dossier `config/` du dépôt est monté dans les conteneurs. Les
  fichiers générés dans les dossiers d’état sont des copies de travail.
- **Raspberry installé comme service natif :** la configuration provient du
  dossier `app/config/` de l’installation TuneSaver.
- **Application et CLI Mac distribuées :** les configurations sont embarquées
  dans leurs paquets respectifs. Elles ne constituent pas actuellement des
  préférences utilisateur ; modifier le paquet signé n’est pas le parcours prévu.

Les plafonds sont locaux à une installation. Deux machines ne partagent pas
leurs compteurs de téléchargement, même si elles utilisent le même compte.

## Repères dans le code

- [`download_engine.py`](../src/download_engine.py) : `worker` traite une tâche
  à la fois ; les verrous `worker` et `operation` sérialisent les opérations
  d’une plateforme. `sync_tiddl_config` et `build_beatport_config` préparent les
  configurations des connecteurs.
- [`desktop.py`](../src/desktop.py) : `start_worker` démarre le processus d’une plateforme.
- [`terminal.py`](../src/terminal.py) : `download` traite les liens successivement.
- [`compose.yaml`](../compose.yaml) : services distincts pour les trois plateformes.

La lecture des playlists, les recherches de correspondances et la synchronisation
du catalogue sont des opérations distinctes : leurs limites de concurrence ne
fixent pas le nombre de morceaux audio téléchargés en parallèle.
