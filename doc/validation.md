# Validation du prototype 0.1.0

Date : 24 novembre 2026.

- 26 tests Python réussis (Python 3.12).
- 8 tests ERT réussis dans GNU Emacs 30.1 en mode batch.
- Compilation du mode Emacs sans erreur ni avertissement.
- Lancement réel d'Emacs 30.1 avec `-nw`, chargement du mode et sortie propre,
  sous un pseudo-terminal fourni par `script`.
- 7 tests du DOM réussis sous Node avec linkedom, sur des pages synthétiques.
- Construction du paquet Debian architecture all avec dpkg-deb.

Les tests de transport utilisent un navigateur émulé qui parle le protocole
sur les vrais pipes FD 3/4. Les tests de navigation, d'envoi et de téléchargement
simulent les réponses du navigateur : ils valident la logique du client, pas
la compatibilité avec le site ChatGPT actuel. Les tests de patches et d'exécution
utilisent réellement Git et le shell sur des fichiers temporaires.

Le démarrage réel de Chromium échoue dans l'environnement de développement :
process_singleton_posix.cc : socket() failed: Operation not permitted.
Le diagnostic public renvoie browser_start. Aucun identifiant n'a été demandé
ou utilisé. Connexion réelle, échanges ChatGPT et transferts via le site restent
à valider sur la machine de l'utilisateur.

Les tests ERT sont exécutés en mode batch ; l'essai de démarrage en terminal ne
constitue pas une validation des échanges avec ChatGPT.
