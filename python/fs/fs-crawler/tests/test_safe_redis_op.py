"""Focused tests for the restored safe_redis_op contract.

The helper was added in 4b5bcfb and removed in 89917512d while its callers
remained.  These tests pin the historical behaviour callers rely on:
degrade to the supplied default on Redis transport failures and on an
uninitialized client, and expose the startup import path again.
"""

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import redis.exceptions  # noqa: E402

import database  # noqa: E402
from services.startup import StartupService  # noqa: E402


class SafeRedisOpTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_value_on_success(self):
        async def operation():
            return "ok"

        self.assertEqual(await database.safe_redis_op(operation), "ok")

    async def test_returns_default_on_connection_error(self):
        async def operation():
            raise redis.exceptions.ConnectionError("redis down")

        self.assertEqual(
            await database.safe_redis_op(operation, default=[], operation="test.connection"),
            [],
        )

    async def test_returns_default_on_timeout(self):
        async def operation():
            raise redis.exceptions.TimeoutError("redis timeout")

        self.assertEqual(
            await database.safe_redis_op(operation, default=-2, operation="test.timeout"),
            -2,
        )

    async def test_returns_default_on_socket_error(self):
        async def operation():
            raise OSError("connection reset")

        self.assertIsNone(await database.safe_redis_op(operation))

    async def test_returns_default_on_uninitialized_client(self):
        def operation():
            # Mirrors a caller capturing redis_client=None at startup.
            return None.keys()

        self.assertEqual(
            await database.safe_redis_op(operation, default=[], operation="test.uninitialized"),
            [],
        )

    async def test_startup_import_path_exposes_service(self):
        self.assertTrue(callable(StartupService))


if __name__ == "__main__":
    unittest.main()
