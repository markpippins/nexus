"""
Database connection management for Redis, MongoDB, and MySQL
"""

import asyncio
from typing import Optional, Any, Callable
from motor.motor_asyncio import AsyncIOMotorClient
import redis.asyncio as redis
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
    TimeoutError as RedisTimeoutError,
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import text
import structlog

# Handle imports differently when run as a script vs module
try:
    from .config import settings
except ImportError:
    # When run as a script, use absolute imports
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent))  # Same directory

    from config import settings

logger = structlog.get_logger()

# Database instances
redis_client: Optional[redis.Redis] = None
mongodb_client: Optional[AsyncIOMotorClient] = None
mongodb_db = None
mysql_engine = None
async_session_maker = None

# SQLAlchemy base
Base = declarative_base()


async def safe_redis_op(coro_factory: Callable, default: Any = None,
                         operation: str = "redis_op") -> Any:
    """Execute a Redis operation with ConnectionError handling.

    Restored from the contract added in 4b5bcfb and removed in 89917512d
    while its callers remained.  Retries are handled by the client's
    built-in Retry policy; this function handles errors that survive retry
    so callers can degrade gracefully instead of propagating exceptions.

    Args:
        coro_factory: Callable that returns an awaitable (e.g. lambda: client.hget(k, f))
        default: Value to return on ConnectionError (None = silent degradation)
        operation: Label for logging on failure

    Returns:
        Result of the operation, or default on ConnectionError.
    """
    try:
        return await coro_factory()
    except RedisConnectionError as e:
        logger.warning("redis_connection_error", operation=operation, error=str(e))
        return default
    except RedisTimeoutError as e:
        logger.warning("redis_timeout", operation=operation, error=str(e))
        return default
    except OSError as e:
        # Lower-level socket errors (e.g. "Connection reset by peer") that
        # may not be wrapped by redis-py in some transport scenarios.
        logger.warning("redis_os_error", operation=operation, error=str(e))
        return default
    except AttributeError:
        # guards against redis_client being None during startup or
        # reconnection — not a normal operational error.
        logger.error("redis_client_uninitialized", operation=operation)
        return default


async def init_databases():
    """Initialize all database connections"""
    global redis_client, mongodb_client, mongodb_db, mysql_engine, async_session_maker
    
    try:
        # Initialize Redis
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        await redis_client.ping()
        logger.info("Redis connection established")
        
        # Initialize MongoDB
        mongodb_client = AsyncIOMotorClient(settings.mongodb_url)
        mongodb_db = mongodb_client[settings.mongodb_database]
        # Test connection
        await mongodb_client.admin.command('ping')
        logger.info("MongoDB connection established")
        
        # Initialize MySQL with async support
        # Convert mysql:// to mysql+aiomysql://
        mysql_url = settings.mysql_url.replace("mysql://", "mysql+aiomysql://")
        mysql_engine = create_async_engine(mysql_url, echo=False)
        async_session_maker = async_sessionmaker(mysql_engine, expire_on_commit=False)
        
        # Test MySQL connection
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
        logger.info("MySQL connection established")
        
        # Create tables if they don't exist
        from models.mysql_models import LibraryPath, FileType, MetadataHandler, HandlerFileType, ScanOperation
        async with mysql_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("MySQL tables initialized")
        
    except Exception as e:
        logger.error("Failed to initialize databases", error=str(e))
        raise


async def close_databases():
    """Close all database connections"""
    global redis_client, mongodb_client, mysql_engine
    
    if redis_client:
        await redis_client.close()
        logger.info("Redis connection closed")
    
    if mongodb_client:
        mongodb_client.close()
        logger.info("MongoDB connection closed")
    
    if mysql_engine:
        await mysql_engine.dispose()
        logger.info("MySQL connection closed")


def get_redis() -> redis.Redis:
    """Get Redis client instance"""
    if redis_client is None:
        raise RuntimeError("Redis client not initialized")
    return redis_client


def get_mongodb():
    """Get MongoDB database instance"""
    if mongodb_db is None:
        raise RuntimeError("MongoDB client not initialized")
    return mongodb_db


async def get_mysql_session() -> AsyncSession:
    """Get MySQL session"""
    if async_session_maker is None:
        raise RuntimeError("MySQL engine not initialized")
    return async_session_maker()