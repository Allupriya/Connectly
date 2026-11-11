"""MongoDB connection and collection helpers using Motor (async driver).

Collection and GridFS bucket names come from settings (env-driven), so nothing
is hard-coded. Use :func:`get_db` for the database handle, the ``*_collection``
helpers for a specific collection, and :func:`get_fs` for the uploads bucket.
"""

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
    AsyncIOMotorGridFSBucket,
)

from ..config import get_logger, settings

logger = get_logger(__name__)

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None
_fs: AsyncIOMotorGridFSBucket | None = None


async def connect_to_mongo() -> None:
    """Open the MongoDB connection and create useful indexes."""
    global _client, _db, _fs
    try:
        settings.validate()
    except Exception:
        logger.exception("MongoDB config invalid (check MONGODB_URL in your .env).")
        raise

    try:
        _client = AsyncIOMotorClient(settings.MONGODB_URL)
        _db = _client[settings.MONGODB_DB]
        _fs = None  # Rebuilt lazily against the (re)connected database.

        # Verify the connection early so misconfiguration fails loudly on startup.
        await _client.admin.command("ping")
        logger.info("Ping to MongoDB '%s' succeeded.", settings.MONGODB_DB)
    except Exception:
        logger.exception(
            "Could not connect to MongoDB. Is the server reachable and the URL correct?"
        )
        raise

    try:
        # Indexes keep lookups fast and usernames unique.
        await users_collection().create_index("username", unique=True)
        # Session tokens are looked up (hashed) on every authenticated request.
        await users_collection().create_index(
            "token_hash", unique=True, sparse=True
        )
        # One message document per (chat, sender, day); this key must be unique so
        # find_one_and_update upserts into the same doc for the whole day.
        await messages_collection().create_index(
            [("chat_id", 1), ("sender_id", 1), ("date", 1)], unique=True
        )
        await chats_collection().create_index("members")
        # One push subscription doc per endpoint; index by user for fast fan-out.
        await push_subscriptions_collection().create_index("endpoint", unique=True)
        await push_subscriptions_collection().create_index("user_id")
        logger.info("MongoDB indexes ensured.")
    except Exception:
        logger.exception("Failed to create MongoDB indexes.")
        raise


async def close_mongo_connection() -> None:
    global _client, _fs
    try:
        if _client is not None:
            _client.close()
            _fs = None
            logger.info("MongoDB client closed.")
    except Exception:
        logger.exception("Error while closing the MongoDB client.")


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database is not initialized. Call connect_to_mongo first.")
    return _db


def users_collection() -> AsyncIOMotorCollection:
    return get_db()[settings.USERS_COLLECTION]


def chats_collection() -> AsyncIOMotorCollection:
    return get_db()[settings.CHATS_COLLECTION]


def messages_collection() -> AsyncIOMotorCollection:
    return get_db()[settings.MESSAGES_COLLECTION]


def push_subscriptions_collection() -> AsyncIOMotorCollection:
    return get_db()[settings.PUSH_SUBSCRIPTIONS_COLLECTION]


def get_fs() -> AsyncIOMotorGridFSBucket:
    """Return the GridFS bucket used to store/serve uploaded files."""
    global _fs
    if _db is None:
        raise RuntimeError("Database is not initialized. Call connect_to_mongo first.")
    if _fs is None:
        _fs = AsyncIOMotorGridFSBucket(_db, bucket_name=settings.UPLOADS_BUCKET)
    return _fs
