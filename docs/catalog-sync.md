# Catalogue global — métadonnées uniquement

Admin → Catalogue global affiche les titres, artistes, plateformes et liens. Le bouton Exporter JSON récupère toute la liste ; la consultation est paginée par 100 titres.

Le serveur collecte les téléchargements réussis toutes les 30 secondes, indépendamment du navigateur. Le Mac collecte ses téléchargements réussis toutes les 15 secondes pendant que l'application est ouverte. Le terminal collecte après chaque téléchargement et à chaque invocation ; `tunesaver sync` force une tentative. Les anciens téléchargements présents dans la file locale sont repris. Pour les albums, les tags des fichiers sont lus localement ; le lien peut être celui de l'album téléchargé. Les anciens fichiers effacés sans métadonnées de titre ne peuvent pas être reconstitués.

Aucun fichier audio, chemin local, pochette ou embedding n'est envoyé : les requêtes ne contiennent que `title`, `artist`, `url`, `provider`. Les autres champs sont refusés. Les titres avec le même identifiant de morceau sur une plateforme sont dédupliqués, même avec des tags différents. Un même morceau provenant de deux plateformes conserve ses deux liens. Les liens d'albums sont distingués par les métadonnées des pistes.

Les références locales sont enregistrées durablement dans `catalog-sync.sqlite`, en permissions 600, puis envoyées par lots de 100. Une requête échouée reste en attente ; une réponse perdue ne crée pas de doublon. Aucun réseau n'est nécessaire pour terminer un téléchargement. Un terminal fermé ne synchronise pas seul : relancer TuneSaver ou `tunesaver sync`.

Les nouveaux fichiers d'export de connexions contiennent une configuration de synchronisation. L'import Mac conserve ce jeton dans le Trousseau. Le terminal utilise le même Trousseau et le même dossier de données ; `tunesaver sync --import-file fichier.json` configure uniquement la synchronisation. Le jeton permet seulement d'envoyer des métadonnées, pas de consulter l'admin ni d'exporter des comptes. Un compte désapprouvé ou supprimé ne peut plus envoyer. Les appareils personnels provisionnés par l'administrateur ont un jeton indépendant. Pour révoquer un appareil : mettre `catalog_devices.revoked=1` pour son token_hash, ou son label unique. Les jetons restent valides jusqu'à révocation.

Le Raspberry utilise `TUNESAVER_CATALOG_CONFIG` vers un fichier privé ; le serveur cible est fixé à `https://tunesaver.galagain.com`, les redirections réseau sont refusées. L'export admin ne contient aucun jeton.

PostgreSQL : appliquer `migrations/003_catalog.sql` avec le rôle de migration avant déploiement. Le compte applicatif ne reçoit que les droits de lecture/écriture. SQLite initialise ces tables automatiquement. Une sauvegarde de l'image et des sources a été conservée avant déploiement.

Tests : `python -m unittest discover -s tests -p test_catalog_sync.py` ; tests d'import de comptes, du terminal et smoke test Mac.
