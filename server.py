import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

from aiohttp import web, WSMsgType

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def load_env_file(path):
    """Charge un fichier .env sans écraser les variables fournies par l'hébergeur."""
    if not path.is_file():
        print(f"[CONFIG] Aucun fichier .env trouvé ({path}); variables système utilisées.")
        return
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            print(f"[CONFIG] Ligne {line_number} ignorée : format attendu NOM=VALEUR.")
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum():
            print(f"[CONFIG] Ligne {line_number} ignorée : nom de variable invalide.")
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\\\"", "'"):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)
    print(f"[CONFIG] Configuration chargée depuis {path.name}.")


load_env_file(ENV_FILE)


def env_int(name, default, minimum, maximum):
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise RuntimeError(f"Configuration invalide : {name} doit être un nombre entier.")
    if not minimum <= value <= maximum:
        raise RuntimeError(f"Configuration invalide : {name} doit être compris entre {minimum} et {maximum}.")
    return value


HOST = os.getenv("HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = env_int("PORT", 8080, 1, 65535)
MAX_PLAYERS = env_int("MAX_PLAYERS", 50, 1, 1000)
SESSION_DURATION = env_int("SESSION_DURATION", 30 * 24 * 60 * 60, 300, 365 * 24 * 60 * 60)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "Lesaxolotls").strip() or "Lesaxolotls"
WORLD_NAME = os.getenv("WORLD_NAME", "ARKADIUM 3D").strip() or "ARKADIUM 3D"
WEB_DIR = BASE_DIR / "web"
DB_PATH = Path(os.getenv("ARKADIUM_DB", str(BASE_DIR / "arkadium.sqlite3"))).expanduser()
if not DB_PATH.is_absolute():
    DB_PATH = (BASE_DIR / DB_PATH).resolve()
COOKIE_NAME = "arkadium_session"
PASSWORD_ROUNDS = 310_000

# Ressources fixes partagees par tous les joueurs.
PICKUP_DEFS = [
    {"id": "wood-1", "type": "wood", "x": 8, "z": 5},
    {"id": "wood-2", "type": "wood", "x": -12, "z": 9},
    {"id": "wood-3", "type": "wood", "x": 18, "z": -14},
    {"id": "wood-4", "type": "wood", "x": -22, "z": -16},
    {"id": "stone-1", "type": "stone", "x": 12, "z": 18},
    {"id": "stone-2", "type": "stone", "x": -18, "z": 22},
    {"id": "stone-3", "type": "stone", "x": 27, "z": 8},
    {"id": "stone-4", "type": "stone", "x": -30, "z": -5},
    {"id": "crystal-1", "type": "crystal", "x": 34, "z": 24},
    {"id": "crystal-2", "type": "crystal", "x": -36, "z": 18},
    {"id": "crystal-3", "type": "crystal", "x": 40, "z": -20},
    {"id": "crystal-4", "type": "crystal", "x": -42, "z": -28},
]
RESOURCE_NAMES = {"wood": "bois", "stone": "pierre", "crystal": "cristal"}
players = {}
sockets = {}
available_pickups = {item["id"]: dict(item) for item in PICKUP_DEFS}


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ROUNDS)
    return salt.hex(), digest.hex()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            username_key TEXT NOT NULL UNIQUE,
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            inventory TEXT NOT NULL DEFAULT '{"wood":0,"stone":0,"crystal":0}',
            role TEXT NOT NULL DEFAULT 'player',
            created_at INTEGER NOT NULL
        )""")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()}
        if "role" not in columns:
            conn.execute("ALTER TABLE accounts ADD COLUMN role TEXT NOT NULL DEFAULT 'player'")
        conn.execute("UPDATE accounts SET role='admin' WHERE username_key=?", (ADMIN_USERNAME.casefold(),))
        conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
        )""")
        # Compte initial : le mot de passe est stocke uniquement sous forme PBKDF2.
        # Compte initial historique conservé pour ne pas casser les installations existantes.
        username = "Lesaxolotls"
        salt, digest = hash_password("Axio,2014", bytes.fromhex("25d11749b4fa9e5368e613b1bda6622a"))
        conn.execute(
            "INSERT OR IGNORE INTO accounts(username, username_key, salt, password_hash, inventory, created_at) VALUES(?,?,?,?,?,?)",
            (username, username.lower(), salt, digest, json.dumps({"wood": 0, "stone": 0, "crystal": 0}), int(time.time()))
        )
        conn.execute("UPDATE accounts SET role='admin' WHERE username_key=?", (ADMIN_USERNAME.casefold(),))
        conn.commit()


def account_from_cookie(request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with db() as conn:
        row = conn.execute(
            """SELECT a.id, a.username, a.inventory, a.role FROM sessions s
               JOIN accounts a ON a.id=s.account_id
               WHERE s.token_hash=? AND s.expires_at>?""",
            (token_hash, int(time.time()))
        ).fetchone()
    if not row:
        return None
    try:
        inventory = json.loads(row["inventory"])
    except (TypeError, json.JSONDecodeError):
        inventory = {"wood": 0, "stone": 0, "crystal": 0}
    return {"id": row["id"], "username": row["username"], "inventory": inventory, "role": row["role"], "is_admin": row["role"] == "admin"}


def set_session(response, account_id, request):
    token = secrets.token_urlsafe(36)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at<=?", (int(time.time()),))
        conn.execute(
            "INSERT INTO sessions(token_hash, account_id, expires_at) VALUES(?,?,?)",
            (token_hash, account_id, int(time.time()) + SESSION_DURATION)
        )
        conn.commit()
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_DURATION, httponly=True,
        secure=request.secure, samesite="Lax", path="/"
    )


async def index(_request):
    return web.FileResponse(WEB_DIR / "index.html")


async def health(_request):
    return web.json_response({
        "status": "ok", "game": WORLD_NAME,
        "players": len(players), "max_players": MAX_PLAYERS
    })


async def register(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Requete invalide."}, status=400)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", username):
        return web.json_response({"error": "Pseudo : 3 a 16 caracteres (lettres, chiffres, _)."}, status=400)
    if len(password) < 8 or len(password) > 128:
        return web.json_response({"error": "Le mot de passe doit contenir entre 8 et 128 caracteres."}, status=400)
    salt, digest = hash_password(password)
    try:
        with db() as conn:
            cursor = conn.execute(
                "INSERT INTO accounts(username, username_key, salt, password_hash, inventory, created_at) VALUES(?,?,?,?,?,?)",
                (username, username.lower(), salt, digest, json.dumps({"wood": 0, "stone": 0, "crystal": 0}), int(time.time()))
            )
            if username.casefold() == ADMIN_USERNAME.casefold():
                conn.execute("UPDATE accounts SET role='admin' WHERE id=?", (cursor.lastrowid,))
            account_id = cursor.lastrowid
            conn.commit()
    except sqlite3.IntegrityError:
        return web.json_response({"error": "Ce pseudo est deja utilise."}, status=409)
    response = web.json_response({"ok": True, "username": username})
    set_session(response, account_id, request)
    return response


async def login(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Requete invalide."}, status=400)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    with db() as conn:
        row = conn.execute(
            "SELECT id, username, salt, password_hash, role FROM accounts WHERE username_key=?",
            (username.lower(),)
        ).fetchone()
    if not row:
        return web.json_response({"error": "Identifiants incorrects."}, status=401)
    _, candidate = hash_password(password, bytes.fromhex(row["salt"]))
    if not hmac.compare_digest(candidate, row["password_hash"]):
        return web.json_response({"error": "Identifiants incorrects."}, status=401)
    response = web.json_response({"ok": True, "username": row["username"], "is_admin": row["role"] == "admin"})
    set_session(response, row["id"], request)
    return response


async def me(request):
    account = account_from_cookie(request)
    if not account:
        return web.json_response({"error": "Non connecte."}, status=401)
    return web.json_response({"username": account["username"], "inventory": account["inventory"], "role": account["role"], "is_admin": account["is_admin"]})


async def logout(request):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
            conn.commit()
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE_NAME, path="/")
    return response


async def send_json(ws, payload):
    await ws.send_str(json.dumps(payload))


async def broadcast(payload, exclude=None):
    message = json.dumps(payload)
    stale = []
    for player_id, ws in list(sockets.items()):
        if player_id == exclude:
            continue
        try:
            await ws.send_str(message)
        except Exception:
            stale.append(player_id)
    for player_id in stale:
        sockets.pop(player_id, None)
        players.pop(player_id, None)


async def handle_command(raw, player_id, account, ws):
    parts = raw.strip().split()
    command, args = parts[0].lower(), parts[1:]
    admin = account.get("is_admin", False)
    current = players.get(player_id)
    if command == "/help":
        await send_json(ws, {"type": "notice", "message": "Commandes : /help, /me. Admin : /admin, /fly, /gamemode, /tp, /heal, /give, /kick."})
        return
    if command == "/me":
        await send_json(ws, {"type": "notice", "message": "Connecté : " + account["username"] + (" (ADMIN)" if admin else " (Joueur)")})
        return
    if command not in ("/admin", "/fly", "/gamemode", "/tp", "/heal", "/give", "/kick"):
        await send_json(ws, {"type": "notice", "message": "Commande inconnue. Tape /help."})
        return
    if not admin:
        await send_json(ws, {"type": "notice", "message": "Commande réservée aux administrateurs."})
        return
    if command == "/admin":
        msg = "ADMIN : /fly · /gamemode creative|survival · /tp pseudo · /heal [pseudo] · /give bois|pierre|cristal quantité · /kick pseudo"
        await send_json(ws, {"type": "notice", "message": msg})
    elif command == "/fly":
        if args:
            await send_json(ws, {"type": "notice", "message": "Utilisation : /fly"})
            return
        current["flying"] = not current.get("flying", False)
        await send_json(ws, {"type": "fly", "enabled": current["flying"]})
        await send_json(ws, {"type": "notice", "message": "Vol activé." if current["flying"] else "Vol désactivé."})
    elif command == "/gamemode":
        if len(args) != 1 or args[0].lower() not in ("creative", "survival"):
            await send_json(ws, {"type": "notice", "message": "Utilisation : /gamemode creative ou /gamemode survival"})
            return
        current["game_mode"] = args[0].lower()
        await send_json(ws, {"type": "gamemode", "mode": current["game_mode"]})
        await send_json(ws, {"type": "notice", "message": "Mode : " + current["game_mode"]})
    elif command == "/tp":
        target = next((p for p in players.values() if args and p["name"].lower() == args[0].lower()), None)
        if not target:
            await send_json(ws, {"type": "notice", "message": "Joueur introuvable. Utilisation : /tp pseudo"})
            return
        current["x"], current["y"], current["z"] = target["x"], target["y"], target["z"]
        await send_json(ws, {"type": "teleport", "x": target["x"], "y": target["y"], "z": target["z"]})
        await send_json(ws, {"type": "notice", "message": "Téléportation vers " + target["name"]})
    elif command == "/heal":
        target = current if not args else next((p for p in players.values() if p["name"].lower() == args[0].lower()), None)
        if len(args) > 1 or not target:
            await send_json(ws, {"type": "notice", "message": "Utilisation : /heal [pseudo] — joueur introuvable."})
            return
        target["health"] = 100
        target_ws = sockets.get(target["id"])
        if target_ws:
            await send_json(target_ws, {"type": "health", "player_id": target["id"], "health": 100})
        await send_json(ws, {"type": "notice", "message": "Vie restaurée : " + target["name"]})
    elif command == "/give":
        names = {"bois": "wood", "pierre": "stone", "cristal": "crystal"}
        if len(args) != 2 or args[0].lower() not in names:
            await send_json(ws, {"type": "notice", "message": "Utilisation : /give bois|pierre|cristal quantité"})
            return
        try:
            amount = int(args[1])
        except ValueError:
            amount = 0
        if amount < 1 or amount > 10000:
            await send_json(ws, {"type": "notice", "message": "Quantité autorisée : 1 à 10000."})
            return
        key = names[args[0].lower()]
        with db() as conn:
            row = conn.execute("SELECT inventory FROM accounts WHERE id=?", (account["id"],)).fetchone()
            inv = json.loads(row["inventory"]) if row else {"wood": 0, "stone": 0, "crystal": 0}
            inv[key] = int(inv.get(key, 0)) + amount
            conn.execute("UPDATE accounts SET inventory=? WHERE id=?", (json.dumps(inv), account["id"]))
            conn.commit()
        await send_json(ws, {"type": "inventory", "inventory": inv})
        await send_json(ws, {"type": "notice", "message": str(amount) + " " + args[0].lower() + " ajouté(s)."})
    elif command == "/kick":
        if len(args) != 1:
            await send_json(ws, {"type": "notice", "message": "Utilisation : /kick pseudo"})
            return
        target = next((p for p in players.values() if p["name"].lower() == args[0].lower()), None)
        if not target:
            await send_json(ws, {"type": "notice", "message": "Joueur introuvable ou hors ligne."})
            return
        target_ws = sockets.get(target["id"])
        if target_ws:
            await send_json(target_ws, {"type": "notice", "message": "Tu as été expulsé par un administrateur."})
            await target_ws.close(code=4001, message=b"Admin kick")
        await send_json(ws, {"type": "notice", "message": target["name"] + " a été expulsé."})


async def websocket_handler(request):
    account = account_from_cookie(request)
    if not account:
        return web.json_response({"error": "Connecte-toi pour jouer."}, status=401)
    if len(players) >= MAX_PLAYERS:
        return web.json_response({"error": "Le serveur est complet."}, status=503)

    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=4096)
    await ws.prepare(request)
    player_id = uuid.uuid4().hex[:8]
    player = {
        "id": player_id, "account_id": account["id"], "name": account["username"],
        "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0,
        "health": 100, "last_attack": 0.0, "game_mode": "survival", "flying": False,
        "anticheat_violations": 0, "last_move_at": time.monotonic(), "last_anticheat_notice": 0.0,
        "color": "#" + uuid.uuid4().hex[:6]
    }
    players[player_id] = player
    sockets[player_id] = ws
    await send_json(ws, {
        "type": "welcome", "id": player_id,
        "players": [p for p in players.values() if p["id"] != player_id],
        "max_players": MAX_PLAYERS, "username": account["username"],
        "inventory": account["inventory"], "is_admin": account["is_admin"],
        "pickups": list(available_pickups.values())
    })
    await broadcast({"type": "join", "player": player}, exclude=player_id)

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                kind = data.get("type")
                if kind == "move":
                    current = players.get(player_id)
                    if not current:
                        continue
                    now = time.monotonic()
                    elapsed = max(0.05, min(2.0, now - current.get("last_move_at", now)))
                    values = [data.get(k) for k in ("x", "y", "z", "yaw")]
                    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
                        continue
                    x, y, z, new_yaw = map(float, values)
                    horizontal_speed = math.hypot(x - current["x"], z - current["z"]) / elapsed
                    vertical_speed = abs(y - current["y"]) / elapsed
                    can_fly = account.get("is_admin", False) and (current.get("flying", False) or current.get("game_mode") == "creative")
                    suspicious = horizontal_speed > 14.5 or (not can_fly and vertical_speed > 16.0) or abs(x) > 10000 or abs(y) > 1000 or abs(z) > 10000
                    exempt = account["username"].casefold() == "lesaxolotls"
                    if suspicious:
                        reason = f"vitesse horizontale={horizontal_speed:.1f}, verticale={vertical_speed:.1f}"
                        if exempt:
                            if now - current.get("last_anticheat_notice", 0.0) >= 10:
                                print(f"[ANTICHEAT] Suspect (exempt, aucune sanction) : {account['username']} ({reason})")
                                current["last_anticheat_notice"] = now
                        else:
                            current["anticheat_violations"] = current.get("anticheat_violations", 0) + 1
                            strikes = current["anticheat_violations"]
                            print(f"[ANTICHEAT] {account['username']} : déplacement suspect ({reason}), {strikes}/6")
                            if strikes in (1, 3):
                                await send_json(ws, {"type": "notice", "message": "Anti-cheat : déplacement suspect détecté."})
                            if strikes >= 6:
                                await send_json(ws, {"type": "notice", "message": "Déconnexion : déplacements impossibles détectés."})
                                await ws.close(code=4002, message=b"Anti-cheat")
                                break
                            await send_json(ws, {
                                "type": "teleport",
                                "x": current["x"], "y": current["y"], "z": current["z"]
                            })
                            current["last_move_at"] = now
                            continue
                    if current.get("anticheat_violations", 0) > 0:
                        current["anticheat_violations"] -= 1
                    current["x"], current["y"], current["z"], current["yaw"] = x, y, z, new_yaw
                    current["last_move_at"] = now
                    await broadcast({"type": "move", "player": current}, exclude=player_id)

                elif kind == "harvest":
                    pickup_id = str(data.get("id", ""))
                    pickup = available_pickups.get(pickup_id)
                    current = players.get(player_id)
                    if not pickup or not current:
                        continue
                    distance = ((pickup["x"] - current["x"]) ** 2 + (pickup["z"] - current["z"]) ** 2) ** 0.5
                    if distance > 3.5:
                        await send_json(ws, {"type": "notice", "message": "Approche-toi davantage de la ressource."})
                        continue
                    available_pickups.pop(pickup_id, None)
                    item_type = pickup["type"]
                    with db() as conn:
                        row = conn.execute("SELECT inventory FROM accounts WHERE id=?", (account["id"],)).fetchone()
                        inventory = json.loads(row["inventory"]) if row else {"wood": 0, "stone": 0, "crystal": 0}
                        inventory[item_type] = int(inventory.get(item_type, 0)) + 1
                        conn.execute("UPDATE accounts SET inventory=? WHERE id=?", (json.dumps(inventory), account["id"]))
                        conn.commit()
                    await send_json(ws, {"type": "inventory", "inventory": inventory})
                    await send_json(ws, {"type": "notice", "message": "Ressource recuperee : " + RESOURCE_NAMES[item_type]})
                    await broadcast({"type": "pickup_removed", "id": pickup_id})

                elif kind == "attack":
                    now = time.monotonic()
                    attacker = players.get(player_id)
                    if not attacker or now - attacker.get("last_attack", 0) < 0.5:
                        continue
                    attacker["last_attack"] = now
                    target_id = None
                    target_distance = 3.2
                    for other_id, other in players.items():
                        if other_id == player_id:
                            continue
                        distance = ((other["x"] - attacker["x"]) ** 2 + (other["z"] - attacker["z"]) ** 2) ** 0.5
                        if distance < target_distance:
                            target_id, target_distance = other_id, distance
                    if target_id is None:
                        await send_json(ws, {"type": "attack_result", "hit": False})
                    else:
                        target = players[target_id]
                        target["health"] = max(0, target.get("health", 100) - 25)
                        await broadcast({"type": "health", "player_id": target_id, "health": target["health"]})
                        await send_json(ws, {"type": "attack_result", "hit": True})
                        if target["health"] <= 0:
                            target["x"] = target["y"] = target["z"] = 0.0
                            target["health"] = 100
                            await broadcast({"type": "respawn", "player_id": target_id, "x": 0, "y": 0, "z": 0})
                            await broadcast({"type": "health", "player_id": target_id, "health": 100})

                elif kind == "chat":
                    message = str(data.get("message", "")).strip()[:240]
                    if message.startswith("/"):
                        await handle_command(message, player_id, account, ws)
                    elif message:
                        await broadcast({"type": "chat", "id": player_id, "name": player["name"], "message": message})
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        sockets.pop(player_id, None)
        players.pop(player_id, None)
        await broadcast({"type": "leave", "id": player_id})
    return ws


def create_app():
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with db() as conn:
            conn.execute("SELECT 1")
        print(f"[CONFIG] Base SQLite : {DB_PATH}")
    except (OSError, sqlite3.Error) as exc:
        raise RuntimeError(
            f"Impossible d'ouvrir la base SQLite : {DB_PATH}. "
            "Vérifie que le dossier existe et que l'hébergeur autorise l'écriture. "
            "Configure ARKADIUM_DB vers le point de montage d'un disque persistant."
        ) from exc
    print(f"[CONFIG] Hôte={HOST} | Port={PORT} | Joueurs max={MAX_PLAYERS} | Admin={ADMIN_USERNAME}")
    init_db()
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_post("/api/register", register)
    app.router.add_post("/api/login", login)
    app.router.add_get("/api/me", me)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/ws", websocket_handler)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host=HOST, port=PORT)
