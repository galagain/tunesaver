# Connexion des appareils

Parcours recommandé : `tunesaver login` dans le terminal, ou **Se connecter avec TuneSaver** dans l’application. Confirmer le code affiché sur la page web ; les comptes et la synchronisation sont configurés automatiquement. Le lien peut être ouvert depuis une autre machine.

## Import manuel (compatibilité)

# Importer les connexions des plateformes

Ouvrir `https://tunesaver.galagain.com/auth/`, utiliser sa session TuneSaver existante dans le même navigateur (ou se connecter), choisir les plateformes et télécharger `tunesaver-accounts.json`. Dans l’application Mac : **Mes plateformes → Importer un fichier**. La connexion manuelle reste disponible.

Un utilisateur doit être approuvé, avoir un e-mail vérifié et ne pas être bloqué. Conformément au choix de l’administrateur, l’approbation du compte donne aussi le droit d’exporter les connexions partagées du serveur, y compris le mot de passe Beatport. La base est relue à chaque export, même si la session est en cache. Une révocation bloque les futurs exports, mais ne peut pas effacer un fichier déjà téléchargé ni les connexions déjà importées.

Une session existante est vérifiée côté serveur, réutilisée et conservée après export. Si elle expire, son jeton de renouvellement est utilisé. Sans session existante, la connexion sur cette page crée une session de cinq minutes sans jeton de renouvellement. Le navigateur ne conserve pas la session dans son stockage et la ferme après l’export. Les réponses sont `no-store`. Déployer derrière HTTPS. Aucun fichier d’authentification n’est accessible comme ressource statique.

Le fichier téléchargé contient des secrets en clair : le garder privé et le supprimer après import. Le Mac enregistre les comptes dans le Trousseau et les fichiers de travail avec les permissions 600. Il conserve les comptes déjà connectés. Les chemins sont fixes, les noms de fichiers et structures sont validés ; les chemins et préférences du serveur Qobuz ne sont pas importés.

Ce transfert ne garantit pas la validité future des sessions : les plateformes peuvent demander une reconnexion ou bloquer un compte. Il n’inclut ni bibliothèque musicale, ni utilisateurs TuneSaver, ni clé administrateur.

Tests : `python -m unittest discover -s tests -p test_account_transfer.py`.
