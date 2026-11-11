"""Real-time messaging, typing indicators and presence over a WebSocket."""

import time
from collections import deque
from urllib.parse import urlparse

from fastapi import (
    APIRouter,
    Query,
    WebSocket,
    WebSocketDisconnect,
)

from ..config import get_logger, settings
from ..db import chats_collection, users_collection
from ..models import now_utc
from ..services import add_message, delete_message, edit_message, manager, push
from ..utils import hash_token, to_object_id

logger = get_logger(__name__)

router = APIRouter()


def _origin_allowed(websocket: WebSocket) -> bool:
    """Guard against cross-site WebSocket hijacking by checking the Origin.

    Browsers always send an ``Origin`` header for WebSocket handshakes, so we
    require it to match our own site (or a configured allow-list). Non-browser
    clients (no Origin) are allowed since they can't be used to hijack a logged
    in browser session.
    """
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    origin = origin.rstrip("/")

    allow = settings.allowed_origins
    if allow:
        return origin in allow

    # No explicit allow-list -> enforce same-origin (Origin host == Host header).
    host = websocket.headers.get("host", "")
    try:
        return urlparse(origin).netloc == host
    except Exception:
        return False


@router.get("/api/ws-info", tags=["WebSocket"], summary="WebSocket protocol reference")
async def ws_info():
    """WebSockets aren't part of the OpenAPI schema, so this documents the protocol.

    Connect to `ws://<host>/ws?user_id=<your user id>`, then exchange JSON frames.
    """
    return {
        "connect": "ws://<host>/ws?user_id=<user_id>",
        "client_to_server": {
            "send_message": {
                "type": "message",
                "chat_id": "<chat_id>",
                "msg_type": "text | image | file",
                "content": "hello",
                "file_url": "(optional) /uploads/...",
                "file_name": "(optional) original name",
            },
            "edit": {"type": "edit", "chat_id": "<chat_id>", "message_id": "<message id>", "content": "updated text"},
            "delete": {"type": "delete", "chat_id": "<chat_id>", "message_id": "<message id>"},
            "read": {"type": "read", "chat_id": "<chat_id>"},
            "typing": {"type": "typing", "chat_id": "<chat_id>", "is_typing": True},
        },
        "server_to_client": {
            "message": {"type": "message", "message": "<message document>"},
            "message_edited": {"type": "message_edited", "message": "<updated message document>"},
            "message_deleted": {"type": "message_deleted", "message": "<tombstoned message document>"},
            "read": {"type": "read", "chat_id": "...", "user_id": "...", "at": "<iso timestamp>"},
            "typing": {"type": "typing", "chat_id": "...", "username": "...", "is_typing": True},
            "presence": {"type": "presence", "user_id": "...", "online": True},
            "presence_snapshot": {"type": "presence_snapshot", "online_users": ["..."]},
            "chat_created": {"type": "chat_created", "chat": "<chat summary>"},
        },
    }


def _sanitize_reply(reply_in) -> dict | None:
    """Keep only the small quoted-preview fields we need for a reply."""
    if not isinstance(reply_in, dict) or not reply_in.get("id"):
        return None
    return {
        "id": str(reply_in.get("id")),
        "sender_username": str(reply_in.get("sender_username") or ""),
        "preview": str(reply_in.get("preview") or "")[:120],
    }


async def broadcast_presence(user_id: str, username: str, online: bool) -> None:
    """Tell everyone who shares a chat with this user about their presence."""
    try:
        chats = await chats_collection().find({"members": user_id}).to_list(length=None)
        peers: set[str] = set()
        for chat in chats:
            peers.update(m for m in chat["members"] if m != user_id)
        await manager.send_to_users(
            peers,
            {"type": "presence", "user_id": user_id, "username": username, "online": online},
        )
    except Exception:
        # Presence is best-effort; never let it break the connection lifecycle.
        logger.exception(
            "broadcast_presence failed (user_id=%s, online=%s).", user_id, online
        )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    if not _origin_allowed(websocket):
        logger.warning("WS rejected: disallowed origin %r", websocket.headers.get("origin"))
        await websocket.close(code=4403)
        return
    try:
        user = await users_collection().find_one({"token_hash": hash_token(token)})
    except Exception:
        logger.exception("WS auth lookup failed.")
        await websocket.close(code=4001)
        return
    if not user:
        logger.warning("WS rejected: invalid session token.")
        await websocket.close(code=4001)
        return

    user_id = str(user["_id"])
    username = user["username"]
    first_connection = await manager.connect(user_id, websocket)
    logger.info("WS connected: user '%s' (%s).", username, user_id)
    if first_connection:
        await broadcast_presence(user_id, username, online=True)

    # Send the current online users to the freshly connected client.
    try:
        await websocket.send_json(
            {"type": "presence_snapshot", "online_users": manager.online_users()}
        )
    except Exception:
        logger.exception("WS failed sending presence snapshot to %s.", user_id)

    # Simple sliding-window rate limit so one connection can't flood the server.
    msg_times: deque[float] = deque()

    try:
        while True:
            data = await websocket.receive_json()

            now = time.monotonic()
            while msg_times and now - msg_times[0] > 10:
                msg_times.popleft()
            if len(msg_times) >= settings.WS_RATE_LIMIT:
                logger.warning("WS rate limit hit for user %s (%s).", username, user_id)
                try:
                    await websocket.send_json(
                        {"type": "error", "detail": "You're sending messages too fast."}
                    )
                except Exception:
                    pass
                continue
            msg_times.append(now)

            # A single bad event must not tear down the whole connection.
            try:
                await handle_ws_event(user_id, username, data)
            except Exception:
                logger.exception(
                    "WS event handling failed (user=%s, event=%r).",
                    user_id,
                    (data or {}).get("type"),
                )
                try:
                    await websocket.send_json(
                        {"type": "error", "detail": "Failed to process your last action."}
                    )
                except Exception:
                    logger.exception("WS could not notify client of the error.")
    except WebSocketDisconnect:
        logger.info("WS disconnected: user '%s' (%s).", username, user_id)
    except Exception:
        # e.g. malformed (non-JSON) frame or unexpected transport error.
        logger.exception("WS connection error for user '%s' (%s).", username, user_id)
    finally:
        now_offline = manager.disconnect(user_id, websocket)
        if now_offline:
            await broadcast_presence(user_id, username, online=False)


def _notification_body(message: dict) -> str:
    """Short preview text for a push notification."""
    msg_type = message.get("msg_type", "text")
    if msg_type == "image":
        return "\U0001F4F7 Photo"
    if msg_type == "file":
        return "\U0001F4CE " + (message.get("file_name") or "File")
    return message.get("content") or "New message"


async def _notify_offline_members(
    chat: dict, members: list, sender_id: str, sender_username: str, message: dict
) -> None:
    """Send a Web Push to chat members who aren't connected right now."""
    try:
        recipients = [
            m for m in members if m != sender_id and not manager.is_online(m)
        ]
        if not recipients:
            return

        is_group = chat.get("type") == "group"
        if is_group:
            title = chat.get("name") or "New message"
            body = f"{sender_username}: {_notification_body(message)}"
        else:
            title = sender_username
            body = _notification_body(message)

        await push.send_to_users(
            recipients,
            {
                "title": title,
                "body": body,
                "chat_id": message.get("chat_id"),
                "url": "/",
            },
        )
    except Exception:
        # Notifications are best-effort; never break message delivery.
        logger.exception("Failed to send push notifications for a new message.")


async def handle_ws_event(user_id: str, username: str, data: dict) -> None:
    event_type = data.get("type")
    chat_id = data.get("chat_id")
    logger.debug("WS event '%s' from %s for chat %s.", event_type, user_id, chat_id)
    if not chat_id:
        logger.warning("WS event '%s' ignored: missing chat_id (user %s).", event_type, user_id)
        return

    chats = chats_collection()
    chat = await chats.find_one({"_id": to_object_id(chat_id)})
    if not chat or user_id not in chat["members"]:
        logger.warning(
            "WS event '%s' denied: user %s not a member of chat %s.",
            event_type,
            user_id,
            chat_id,
        )
        return
    members = chat["members"]

    if event_type == "message":
        content = (data.get("content") or "").strip()[: settings.MAX_MESSAGE_LENGTH]
        msg_type = data.get("msg_type", "text")
        if msg_type not in ("text", "image", "file"):
            logger.warning("WS message rejected: bad msg_type %r.", msg_type)
            return
        if msg_type == "text" and not content:
            return

        message = await add_message(
            chat_id=chat_id,
            sender_id=user_id,
            sender_username=username,
            msg_type=msg_type,
            content=content,
            file_url=data.get("file_url"),
            file_name=data.get("file_name"),
            reply_to=_sanitize_reply(data.get("reply_to")),
        )
        await manager.send_to_users(
            members, {"type": "message", "message": message}
        )
        # Push-notify members who aren't currently connected (app closed/backgrounded).
        await _notify_offline_members(chat, members, user_id, username, message)

    elif event_type == "delete":
        message_id = data.get("message_id")
        if not message_id:
            return
        deleted = await delete_message(
            message_id=message_id, requester_id=user_id, chat_id=chat_id
        )
        if deleted:
            await manager.send_to_users(
                members, {"type": "message_deleted", "message": deleted}
            )

    elif event_type == "read":
        at = now_utc().isoformat()
        await chats.update_one(
            {"_id": to_object_id(chat_id)},
            {"$set": {f"last_read.{user_id}": at}},
        )
        await manager.send_to_users(
            [m for m in members if m != user_id],
            {"type": "read", "chat_id": chat_id, "user_id": user_id, "at": at},
        )

    elif event_type == "edit":
        message_id = data.get("message_id")
        content = (data.get("content") or "").strip()[: settings.MAX_MESSAGE_LENGTH]
        if not message_id or not content:
            return
        edited = await edit_message(
            message_id=message_id, sender_id=user_id, content=content
        )
        if edited:
            await manager.send_to_users(
                members, {"type": "message_edited", "message": edited}
            )

    elif event_type == "typing":
        await manager.send_to_users(
            [m for m in members if m != user_id],
            {
                "type": "typing",
                "chat_id": chat_id,
                "user_id": user_id,
                "username": username,
                "is_typing": bool(data.get("is_typing")),
            },
        )
