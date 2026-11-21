# ARKADIUM 3D

Prototype de jeu de survie 3D jouable dans un navigateur, avec déplacements et chat multijoueur.

## Fonctionnalités actuelles

- Monde 3D dans le navigateur avec Three.js.
- Déplacements ZQSD ou WASD, saut et sprint.
- Caméra à la première ou troisième personne (touche V).
- Souris pour orienter la caméra.
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

## Configuration

- `PORT` : port HTTP, valeur par défaut 8080.
- `MAX_PLAYERS` : nombre maximum de connexions simultanées, valeur par défaut 50.

## État du prototype et limites

Ce dépôt contient une base jouable, pas encore un jeu de survie complet. Les comptes, sauvegardes persistantes, inventaire, fabrication, construction, modération avancée et anti-triche restent à développer. La limite de 50 joueurs est un plafond configuré, pas une capacité validée par un test de charge : les performances dépendront de l'hébergeur. Le navigateur doit pouvoir accéder au CDN Three.js.
