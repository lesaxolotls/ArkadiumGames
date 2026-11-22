import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from aiohttp import web, WSMsgType

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8080"))
MAX_PLAYERS = int(os.getenv("MAX_PLAYERS", "50"))
WEB_DIR = Path(__file__).parent / "web"

players = {}
sockets = {}


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


async def index(_request):
    return web.FileResponse(WEB_DIR / "index.html")


async def health(_request):
    return web.json_response({
        "status": "ok",
        "game": "ARKADIUM 3D",
        "players": len(players),
        "max_players": MAX_PLAYERS
    })


async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=4096)
    await ws.prepare(request)

    if len(players) >= MAX_PLAYERS:
        await send_json(ws, {"type": "error", "message": "Le serveur est complet."})
        await ws.close()
        return ws

    player_id = uuid.uuid4().hex[:8]
    player = {
        "id": player_id,
        "name": "Joueur",
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "yaw": 0.0,
        "health": 100,
        "last_attack": 0.0,
        "color": "#" + uuid.uuid4().hex[:6]
    }
    players[player_id] = player
    sockets[player_id] = ws

    await send_json(ws, {
        "type": "welcome",
        "id": player_id,
        "players": list(players.values()),
        "max_players": MAX_PLAYERS
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
                    for key, low, high in (
                        ("x", -10000, 10000),
                        ("y", -100, 1000),
                        ("z", -10000, 10000),
                        ("yaw", -100000, 100000),
                    ):
                        value = data.get(key)
                        if isinstance(value, (int, float)) and low <= value <= high:
                            current[key] = float(value)
                    await broadcast({"type": "move", "player": current}, exclude=player_id)

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
                            target["x"] = 0.0
                            target["y"] = 0.0
                            target["z"] = 0.0
                            target["health"] = 100
                            await broadcast({"type": "respawn", "player_id": target_id, "x": 0, "y": 0, "z": 0})
                            await broadcast({"type": "health", "player_id": target_id, "health": 100})

                elif kind == "name":
                    name = str(data.get("name", "Joueur")).strip()[:16]
                    players[player_id]["name"] = name or "Joueur"
                    await broadcast({"type": "rename", "id": player_id, "name": players[player_id]["name"]})

                elif kind == "chat":
                    message = str(data.get("message", "")).strip()[:240]
                    if message:
                        await broadcast({
                            "type": "chat",
                            "id": player_id,
                            "name": players[player_id]["name"],
                            "message": message
                        })
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        sockets.pop(player_id, None)
        players.pop(player_id, None)
        await broadcast({"type": "leave", "id": player_id})

    return ws


def create_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/ws", websocket_handler)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host=HOST, port=PORT)
