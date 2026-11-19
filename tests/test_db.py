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

    async def test_new_container_uses_configured_terminal_defaults(self) -> None:
        record = await self.db.create_user_container(
            user_id=789,
            guild_id=2,
            thread_id=31,
            container_id="custom",
            container_name="discord-alpine-789",
            terminal_columns=100,
            terminal_rows=17,
            terminal_idle_minutes=30,
            terminal_rich_embed=False,
            terminal_refresh_seconds=8,
        )

        self.assertEqual(record.terminal_columns, 100)
        self.assertEqual(record.terminal_rows, 17)
        self.assertEqual(record.terminal_idle_minutes, 30)
        self.assertEqual(record.terminal_rich_embed, 0)
        self.assertEqual(record.terminal_refresh_seconds, 8)

        saved = await self.db.get_user_container(789)
        self.assertEqual(saved, record)

    async def test_display_mode_and_message_are_persistent(self) -> None:
        await self.db.create_user_container(
            user_id=790,
            guild_id=2,
            thread_id=32,
            container_id="xorg",
            container_name="discord-alpine-790",
        )

        await self.db.set_display(
            790,
            mode="xorg",
            terminal_message_id=None,
            xorg_message_id=999,
        )

        saved = await self.db.get_user_container(790)
        self.assertEqual(saved.display_mode, "xorg")  # type: ignore[union-attr]
        self.assertEqual(saved.xorg_message_id, 999)  # type: ignore[union-attr]
        self.assertIsNone(saved.terminal_message_id)  # type: ignore[union-attr]


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
            self.assertEqual(record.terminal_rich_embed, 1)  # type: ignore[union-attr]
            self.assertEqual(record.terminal_refresh_seconds, 3)  # type: ignore[union-attr]
            self.assertIsNone(record.terminal_message_id)  # type: ignore[union-attr]
            self.assertEqual(record.display_mode, "tty")  # type: ignore[union-attr]
            self.assertIsNone(record.xorg_message_id)  # type: ignore[union-attr]
            await database.close()


if __name__ == "__main__":
    unittest.main()
