"""Database package: MongoDB connection, collection and GridFS helpers."""

from .database import (
    chats_collection,
    close_mongo_connection,
    connect_to_mongo,
    get_db,
    get_fs,
    messages_collection,
    push_subscriptions_collection,
    users_collection,
)

__all__ = [
    "connect_to_mongo",
    "close_mongo_connection",
    "get_db",
    "get_fs",
    "users_collection",
    "chats_collection",
    "messages_collection",
    "push_subscriptions_collection",
]
