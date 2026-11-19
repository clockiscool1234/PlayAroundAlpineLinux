from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.db import Database


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.temp_dir.cleanup()

    async def test_one_container_per_user_globally(self) -> None:
        created = await self.db.create_user_container(
            user_id=123,
            guild_id=1,
            thread_id=10,
            container_id="abc",
            container_name="discord-alpine-123",
        )
        self.assertEqual(created.container_id, "abc")
        self.assertEqual((await self.db.get_user_container(123)).guild_id, 1)  # type: ignore[union-attr]

        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.create_user_container(
                user_id=123,
                guild_id=2,
                thread_id=20,
                container_id="def",
                container_name="discord-alpine-123",
            )

    async def test_thread_lookup_status_and_delete(self) -> None:
        await self.db.create_user_container(
            user_id=456,
            guild_id=2,
            thread_id=30,
            container_id="xyz",
            container_name="discord-alpine-456",
        )
        record = await self.db.get_by_thread(30)
        self.assertIsNotNone(record)
        self.assertEqual(record.user_id, 456)  # type: ignore[union-attr]

        await self.db.update_status(456, 2, "exited")
        record = await self.db.get_user_container(456)
        self.assertEqual(record.status, "exited")  # type: ignore[union-attr]

        await self.db.delete_user_container(456, 2)
        self.assertIsNone(await self.db.get_user_container(456))


class DatabaseMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_columns_are_added_to_old_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "old.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE user_containers (
                    user_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    thread_id INTEGER NOT NULL,
                    container_id TEXT NOT NULL,
                    container_name TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running'
                )
                """
            )
            connection.execute(
                "INSERT INTO user_containers VALUES (1, 2, 3, 'abc', 'name', 4, 'running')"
            )
            connection.commit()
            connection.close()

            database = Database(path)
            await database.connect()
            record = await database.get_user_container(1)
            self.assertIsNotNone(record)
            self.assertEqual(record.terminal_columns, 80)  # type: ignore[union-attr]
            self.assertEqual(record.terminal_rows, 22)  # type: ignore[union-attr]
            self.assertEqual(record.terminal_idle_minutes, 10)  # type: ignore[union-attr]
            self.assertIsNone(record.terminal_message_id)  # type: ignore[union-attr]
            await database.close()


if __name__ == "__main__":
    unittest.main()
