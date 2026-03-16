from __future__ import annotations

import asyncio
import json
from collections.abc import Set

from fastapi import WebSocket, WebSocketDisconnect

active_connections: Set[WebSocket] = set()


async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    active_connections.add(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        active_connections.discard(websocket)


async def broadcast(event_type: str, payload: dict) -> None:
    message = json.dumps({"type": event_type, "data": payload})
    dead: list[WebSocket] = []
    for ws in active_connections:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        active_connections.discard(ws)
