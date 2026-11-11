"""Tracks live WebSocket connections and routes events to the right users."""

from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        # One user may be connected from several tabs/devices at once.
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: str, websocket: WebSocket) -> bool:
        """Register a socket. Returns True if this is the user's first connection."""
        await websocket.accept()
        was_offline = len(self._connections[user_id]) == 0
        self._connections[user_id].add(websocket)
        return was_offline

    def disconnect(self, user_id: str, websocket: WebSocket) -> bool:
        """Remove a socket. Returns True if the user now has no connections left."""
        sockets = self._connections.get(user_id)
        if not sockets:
            return True
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(user_id, None)
            return True
        return False

    def is_online(self, user_id: str) -> bool:
        return bool(self._connections.get(user_id))

    def online_users(self) -> list[str]:
        return list(self._connections.keys())

    async def send_to_user(self, user_id: str, message: dict) -> None:
        for websocket in list(self._connections.get(user_id, set())):
            try:
                await websocket.send_json(message)
            except Exception:
                # Socket is dead; drop it silently.
                self.disconnect(user_id, websocket)

    async def send_to_users(self, user_ids, message: dict) -> None:
        for user_id in set(user_ids):
            await self.send_to_user(str(user_id), message)


manager = ConnectionManager()
