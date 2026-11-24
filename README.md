# ARKADIUM 3D

Prototype de jeu de survie 3D jouable dans un navigateur, avec déplacements et chat multijoueur.

## Fonctionnalités actuelles

- Monde 3D dans le navigateur avec Three.js.
- Déplacements ZQSD ou WASD, saut et sprint.
- Caméra à la première ou troisième personne (touche V).
- Souris, manette et commandes tactiles pour orienter la caméra et se déplacer sur mobile.
- Joystick virtuel mobile : déplacement, saut, sprint, attaque, récolte et changement de caméra.
- Carte procédurale avec graine aléatoire par démarrage du serveur : forêts, ressources et habitations abandonnées partagées entre les joueurs.
- Mode construction inspiré des jeux de survie : fondations, murs, portes, générateurs, batteries et lampes.
- Constructions synchronisées en multijoueur et sauvegardées dans SQLite.
- Électricité simplifiée : un générateur alimente les lampes proches ; une batterie proche du générateur étend le réseau.
- Joueurs synchronisés en temps réel par WebSocket.
- Chat textuel et compteur de joueurs.
- Endpoint de santé : `/health`.

## Lancer en local (Windows)

1. Installer Python 3.12 ou plus récent.
2. Ouvrir un terminal dans le dossier du projet.
3. Installer les dépendances :

   ```powershell
   py -m pip install -r requirements.txt
   ```

4. Démarrer :

   ```powershell
   py server.py
   ```

5. Ouvrir http://localhost:8080

## Déploiement sur un hébergeur Python

- Version : Python 3.12
- Fichier de démarrage : `server.py`
- Commande : `python server.py`
- Port : utiliser la variable d'environnement `PORT` fournie par l'hébergeur.
- Vérification : ouvrir `/health`.
- WebSocket : `/ws` (même domaine et même port que le site).

La configuration Docker est également fournie.

## Configuration avec `.env`

Au démarrage, le serveur lit automatiquement le fichier `.env` situé à la racine du projet. Le fichier `.env.example` fourni sert de modèle : copie-le sous le nom `.env`, puis adapte les valeurs.

Les variables déjà définies par l'hébergeur ont la priorité sur celles du fichier `.env` (notamment `PORT`).

| Variable | Rôle | Valeur par défaut |
|---|---|---|
| `HOST` | Adresse d'écoute du serveur | `0.0.0.0` |
| `PORT` | Port HTTP fourni par l'hébergeur | `8080` |
| `MAX_PLAYERS` | Nombre maximum de joueurs simultanés | `50` |
| `ARKADIUM_DB` | Chemin du fichier SQLite | `./arkadium.sqlite3` |
| `ADMIN_USERNAME` | Pseudo d'un compte déjà créé qui reçoit le rôle administrateur | `Lesaxolotls` |
| `SESSION_DURATION` | Durée des sessions en secondes | `2592000` (30 jours) |
| `WORLD_NAME` | Nom affiché par le contrôle de santé | `ARKADIUM 3D` |

**Important pour l'hébergement :** configure `ARKADIUM_DB` vers un dossier situé sur un disque/volume persistant et accessible en écriture, par exemple `/data/arkadium.sqlite3` si ton hébergeur monte un volume sur `/data`. Ne choisis pas un dossier temporaire si tu veux conserver les comptes et inventaires après redémarrage.

Le serveur vérifie l'accès à SQLite au démarrage et affiche un message explicite si le chemin est inaccessible. Le pseudo indiqué dans `ADMIN_USERNAME` doit déjà correspondre à un compte existant : le serveur ne donne jamais les droits administrateur à quelqu'un simplement parce qu'il vient de s'inscrire avec ce pseudo. Le rôle administrateur est attribué au compte configuré au démarrage. Les tables existantes sont conservées ; elles ne sont pas supprimées ni réinitialisées.

## État du prototype et limites

Ce dépôt contient une base jouable, pas encore un jeu de survie complet. Les comptes, inventaires et constructions sont sauvegardés dans SQLite. La carte et les emplacements de ressources sont régénérés au redémarrage du serveur ; tous les joueurs connectés partagent la même graine de monde. Le système électrique est une première simulation simple, pas encore un réseau de câbles ou une consommation de carburant. La limite de 50 joueurs est un plafond configuré, pas une capacité validée par un test de charge : les performances dépendront de l'hébergeur. Le navigateur doit pouvoir accéder au CDN Three.js.
