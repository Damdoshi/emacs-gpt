# ChatGPT Web pour Emacs — prototype 0.1.0

Un client écrit pour ce projet : un mode Emacs et un compagnon Python qui pilote
le site ChatGPT dans Chromium sans affichage. Le fonctionnement courant est prévu
sur **la machine où tourne `emacs -nw`**, y compris après une connexion SSH.

Les conversations restent celles du compte sur le site. Le client ne demande
aucune clé API et n'utilise pas la plateforme API facturée à l'usage.
Il ne dépend d'aucun paquet ELPA ni d'un client ChatGPT existant.

**État : prototype à valider avec votre compte.** Les tests locaux couvrent le
protocole, les actions sur fichiers, le mode Emacs et l'analyse du DOM sur des
pages de test. L'environnement de développement empêche le démarrage réel de
Chromium (création d'une socket système refusée) ; aucune connexion à un compte,
aucun véritable échange ChatGPT et aucun transfert réel via le site n'y ont été
validés. Les sélecteurs fournis sont des hypothèses centralisées et modifiables,
pas une garantie de compatibilité avec la version du site de votre compte.

## Installation Debian

Sur la machine où vous utiliserez Emacs :

```sh
sudo apt install ./emacs-chatgpt-web_0.1.0-1_all.deb
chatgpt-web doctor
chatgpt-web doctor --probe
```

APT installe les dépendances classiques : Python 3, Chromium, Git et Emacs.
`emacs-nox` est le premier choix si Emacs n'est pas déjà présent. Aucun `pip`,
`npm`, téléchargement de navigateur à l'exécution ou gestionnaire ELPA n'est utilisé.
Le paquet ne crée aucun service système et ne nécessite pas de privilèges après
son installation. Chromium est lancé avec son sandbox habituel.

L'installation enregistre les commandes Emacs via `site-start.d`. Dans un Emacs
déjà ouvert, on peut charger immédiatement le mode :

```elisp
(load "/usr/share/emacs/site-lisp/chatgpt-web/chatgpt-web.el")
```

Avec `emacs -Q`, qui ignore la configuration du site, utiliser la même expression
ou ajouter explicitement ce répertoire au `load-path`.

## Première connexion

Depuis Emacs, lancer **`M-x chatgpt-web-login`**. Le tampon représente les champs
et boutons réellement présents dans la page de connexion. Sélectionner un champ
ou un bouton avec TAB puis Entrée, ou à la souris lorsqu'elle est disponible.
`g` actualise le formulaire après une navigation.

On peut aussi se connecter avant de lancer Emacs :

```sh
chatgpt-web auth
```

Le menu numéroté suit les étapes du site : choix de la méthode de connexion,
adresse e-mail, mot de passe, code à usage unique, etc. Après avoir rempli un
champ, sélectionner le bouton qui poursuit la connexion. Le mot de passe et les
codes sont saisis sans écho. Les champs déjà remplis ne sont pas renvoyés dans
l'état du formulaire. Les secrets ne sont ni mis en argument d'un programme,
ni écrits dans une configuration ou un journal par le client. Le navigateur
conserve cependant ses cookies de session, comme un navigateur classique.

**Le couple login/mot de passe ne suffit pas toujours.** Certaines connexions
Google/Microsoft/Apple, MFA, passkeys ou vérifications visuelles peuvent exiger
une intervention que cette interface textuelle ne sait pas présenter. Le client
signale ce cas et ne contourne aucune protection. Une connexion purement en
terminal n'est donc pas garantie pour tous les comptes.

Pour une authentification interactive facultative sur une machine avec affichage,
fermer le compagnon puis utiliser le même profil :

```sh
chatgpt-web --headed auth
```

Cette commande ne fournit pas à elle seule un affichage distant sur une machine
sans X. Le déport ponctuel d'une authentification visuelle n'est pas implémenté.
Le mode normal, après authentification, reste sans affichage.

Le profil par défaut est `$XDG_DATA_HOME/chatgpt-web/profile`, soit généralement
`~/.local/share/chatgpt-web/profile`. Il est privé (droits 0700), dédié à ce client
et verrouillé pendant son utilisation. Fermer un compagnon avec
`M-x chatgpt-web-disconnect` avant d'utiliser `chatgpt-web auth` sur le même profil.
La fermeture conserve la session. Pour effacer complètement les données du
client, fermer Emacs/le compagnon et supprimer ce profil dédié.

## Conversations et messages

`M-x chatgpt-web` ouvre le menu principal. Les commandes directes sont :

| Commande | Action |
| --- | --- |
| `chatgpt-web-new` | Ouvrir une nouvelle conversation ; elle est créée sur le site lors du premier envoi |
| `chatgpt-web-conversations` | Afficher les conversations actuellement chargées dans la barre latérale du site |
| `chatgpt-web-open` | Reprendre une conversation par URL `https://chatgpt.com/c/…` ou identifiant |
| `chatgpt-web-login` | Ouvrir les étapes de connexion |
| `chatgpt-web-disconnect` | Fermer le compagnon et le navigateur en conservant la session |

La liste de conversations n'est pas une recherche exhaustive dans tout
l'historique : les anciennes conversations non chargées sont accessibles par
leur URL. Les liens publics `/share/…`, les conversations supprimées et les URL
de projet ne sont pas pris en charge dans cette version.

Dans un tampon de conversation :

| Touche | Action |
| --- | --- |
| `m` | Ouvrir le tampon de rédaction |
| `g` | Actualiser |
| `u` | Joindre un fichier au prochain message |
| `d` | Choisir un fichier à télécharger |
| `w` | Choisir le dossier de travail |
| `e` | Exécuter le bloc sous le curseur |
| `p` | Appliquer le bloc sous le curseur comme patch |
| `s` | Demander l'arrêt de la génération |
| `C-c C-n` | Nouvelle conversation |

Dans le tampon de rédaction, `C-c C-c` envoie et `C-c C-k` masque le brouillon.
La réception est asynchrone : aucun changement de fenêtre n'est imposé lorsqu'une
réponse arrive. Vous pouvez travailler dans d'autres tampons. Un brouillon est
conservé après une erreur ; après un succès, un nouveau texte écrit entre-temps
n'est pas effacé. Les tampons restent disponibles tant qu'Emacs tourne ; cette
version ne fournit pas de démon indépendant survivant à sa fermeture.

Un seul navigateur et un seul envoi sont actifs à la fois. Les autres conversations
peuvent rester ouvertes dans des tampons. Le client empêche de changer de
conversation pendant une génération et de perdre des pièces jointes en attente.
Les reprises automatiques d'envois après erreur sont désactivées pour éviter les
doublons : en cas de délai dépassé, actualiser avant d'envisager de renvoyer.

## Fichiers, scripts et patches

Tout s'applique à la machine où tourne Emacs, sans TRAMP. Le dossier de travail
initial est celui du lancement d'Emacs ; le dernier dossier explicitement choisi
devient ensuite le défaut commun à toutes les conversations pour la session.
`w` ou `M-x chatgpt-web-set-directory` le change. `C-u e` et `C-u p` permettent
aussi de le préciser avant l'action. L'en-tête indique machine et dossier.

Les téléchargements demandent une destination locale et refusent d'écraser un
fichier existant. Les fichiers sont écrits avec des droits privés. Les liens
expirés et certaines vues de la bibliothèque nécessitant des actions
supplémentaires peuvent échouer : aucun accès aux fichiers indisponibles sur le
site n'est inventé. Joindre un fichier sélectionne le fichier dans le site ; son
analyse et son acceptation restent celles de ChatGPT. Attendre la fin du
traitement avant d'envoyer le message si le site l'exige.

Chaque bloc de code offre les actions **Exécuter**, **Appliquer le patch** et
**Enregistrer**. Aucune réponse n'est exécutée automatiquement. Le script choisi
est placé dans un fichier temporaire privé, puis lancé avec l'interpréteur
correspondant dans le dossier sélectionné. Un interpréteur est demandé pour une
langue inconnue. La sortie et le code de retour apparaissent dans un tampon
distinct. Les arguments et le chemin du fichier sont transmis séparément ; le
texte du script n'est pas interpolé dans une commande shell du client.

Un lien nommé `.patch` ou `.diff` propose aussi **Télécharger et appliquer**.
Pour un patch déjà téléchargé, utiliser `M-x chatgpt-web-apply-patch-file`.
Le compagnon vérifie d'abord le patch avec `git apply --check`, puis l'applique
avec `git apply`. Il ne fait ni commit, ni staging, et n'utilise pas `--reject`.
Le format unifié simple et le format Git sont acceptés, même hors d'un dépôt.
Dans un dépôt, sélectionner sa racine comme dossier de travail. Les patches qui
sortent du dossier ou tentent d'écrire à travers un lien symbolique sont refusés
par Git. Si un fichier du projet a des modifications non enregistrées dans
Emacs, l'action demande de les enregistrer avant de poursuivre.
Après succès, les tampons ouverts sans modifications locales sont relus pour
afficher les changements du patch. De nouveaux edits effectués pendant l'action
sont conservés.

Les scripts s'exécutent avec les droits de votre utilisateur : lancer une action
sur un bloc revient à choisir d'exécuter ce code sur la machine indiquée. Ils
n'ont pas de sandbox propre. Le client ne lance pas automatiquement `sudo`.

## HTTPS et fonctionnement local

Le site et les domaines de connexion autorisés sont utilisés en HTTPS, avec la
validation normale des certificats. Les requêtes HTTP/WS non chiffrées sont
bloquées dans l'onglet piloté. Les domaines de connexion acceptés sont listés
explicitement dans `chatgpt_web/web.py` ; les saisies sont refusées sur une autre
origine. Un fournisseur SSO d'entreprise supplémentaire peut nécessiter une
adaptation de cette liste.

Emacs communique avec Python par stdin/stdout ; Python communique avec Chromium
par le pipe DevTools du navigateur. Aucun serveur HTTP local, port TCP d'écoute,
clé API ou accès au profil de votre navigateur habituel n'est nécessaire.

## Maintenance des sélecteurs

Tous les accès au DOM sont dans `chatgpt_web/page.js` et `selectors.json`.
Pour surcharger des sélecteurs sans modifier les fichiers du paquet :

```elisp
(setq chatgpt-web-program-arguments
      '("--selectors" "/home/mon-compte/.config/chatgpt-web/selectors.json"))
```

Le fichier est un objet JSON qui remplace seulement les clés choisies, par
exemple `{"prompt":"#prompt-textarea"}`. Les changements structurels du site
peuvent demander une modification du code de l'adaptateur.

## Construire et tester

Depuis l'archive des sources :

```sh
make check
make deb
```

`make check` nécessite Python 3, Git et Emacs 28 ou plus récent. Pour les tests DOM
de développement facultatifs, installer `linkedom` dans un répertoire de test et
lancer `NODE_PATH=/chemin/node_modules node tests/test_page.js`.
Node et linkedom ne sont pas des dépendances du paquet livré.

`make deb` construit sans root avec `dpkg-deb`. Une arborescence Debian standard
est également fournie pour `dpkg-buildpackage -us -uc -b` avec debhelper 13.
Le champ Maintainer doit être remplacé par le mainteneur réel avant une
publication dans un dépôt. Licence : GPL-3.0-ou-ultérieure.

## Périmètre et conditions du service

Ce logiciel utilise le site tel qu'il est présenté au compte connecté. Les
limites d'usage, fonctions, modèles disponibles et règles du site continuent de
s'appliquer. Aucune compatibilité particulière avec Canvas, la voix, les
passkeys, les outils de validation interactifs ou les résultats multimédias
complexes n'est annoncée.

Les conditions européennes d'OpenAI comportent une restriction sur l'extraction
automatique ou programmatique de données et de réponses. L'objectif d'interface
personnelle déportée ne constitue pas une autorisation explicite de l'opérateur.
Une diffusion du code n'est pas une garantie que son utilisation sur le service
est autorisée : https://openai.com/policies/eu-terms-of-use/.

Documentation officielle sur les étapes de connexion :
https://help.openai.com/en/articles/7426629-why-cant-i-log-in-to-chatgpt.
