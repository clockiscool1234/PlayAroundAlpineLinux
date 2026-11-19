from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiosqlite


@dataclass
class UserContainer:
    user_id: int
    guild_id: int
    thread_id: int
    container_id: str
    container_name: str
    created_at: int
    status: str
    terminal_columns: int = 80
    terminal_rows: int = 22
    terminal_idle_minutes: int = 10
    terminal_rich_embed: int = 1
    terminal_refresh_seconds: int = 3
    terminal_message_id: int | None = None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")
        await self._init_schema()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected")
        return self._db

    async def _init_schema(self) -> None:
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_containers (
                user_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                container_id TEXT NOT NULL,
                container_name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                terminal_columns INTEGER NOT NULL DEFAULT 80,
                terminal_rows INTEGER NOT NULL DEFAULT 22,
                terminal_idle_minutes INTEGER NOT NULL DEFAULT 10,
                terminal_rich_embed INTEGER NOT NULL DEFAULT 1,
                terminal_refresh_seconds INTEGER NOT NULL DEFAULT 3,
                terminal_message_id INTEGER
            )
            """
        )
        # Migrate databases created by the first version of the project.
        cursor = await self.db.execute("PRAGMA table_info(user_containers)")
        columns = {row[1] for row in await cursor.fetchall()}
        migrations = {
            "terminal_columns": (
                "ALTER TABLE user_containers "
                "ADD COLUMN terminal_columns INTEGER NOT NULL DEFAULT 80"
            ),
            "terminal_rows": (
                "ALTER TABLE user_containers "
                "ADD COLUMN terminal_rows INTEGER NOT NULL DEFAULT 22"
            ),
            "terminal_idle_minutes": (
                "ALTER TABLE user_containers "
                "ADD COLUMN terminal_idle_minutes INTEGER NOT NULL DEFAULT 10"
            ),
            "terminal_rich_embed": (
                "ALTER TABLE user_containers "
                "ADD COLUMN terminal_rich_embed INTEGER NOT NULL DEFAULT 1"
            ),
            "terminal_refresh_seconds": (
                "ALTER TABLE user_containers "
                "ADD COLUMN terminal_refresh_seconds INTEGER NOT NULL DEFAULT 3"
            ),
            "terminal_message_id": (
                "ALTER TABLE user_containers ADD COLUMN terminal_message_id INTEGER"
            ),
        }
        for name, statement in migrations.items():
            if name not in columns:
                await self.db.execute(statement)
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_thread ON user_containers(thread_id)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_container ON user_containers(container_id)"
        )
        await self.db.commit()

    async def get_user_container(self, user_id: int) -> Optional[UserContainer]:
        cur = await self.db.execute(
            "SELECT * FROM user_containers WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return UserContainer(**dict(row))

    async def get_by_thread(self, thread_id: int) -> Optional[UserContainer]:
        cur = await self.db.execute(
            "SELECT * FROM user_containers WHERE thread_id = ?",
            (thread_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return UserContainer(**dict(row))

    async def create_user_container(
        self,
        *,
        user_id: int,
        guild_id: int,
        thread_id: int,
        container_id: str,
        container_name: str,
        status: str = "running",
    ) -> UserContainer:
        created_at = int(time.time())
        await self.db.execute(
            """
            INSERT INTO user_containers
                (user_id, guild_id, thread_id, container_id, container_name, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, guild_id, thread_id, container_id, container_name, created_at, status),
        )
        await self.db.commit()
        return UserContainer(
            user_id=user_id,
            guild_id=guild_id,
            thread_id=thread_id,
            container_id=container_id,
            container_name=container_name,
            created_at=created_at,
            status=status,
        )

    async def update_status(self, user_id: int, guild_id: int, status: str) -> None:
        await self.db.execute(
            "UPDATE user_containers SET status = ? WHERE user_id = ? AND guild_id = ?",
            (status, user_id, guild_id),
        )
        await self.db.commit()

    async def update_terminal_settings(
        self,
        user_id: int,
        *,
        columns: int,
        rows: int,
        idle_minutes: int,
        rich_embed: bool,
        refresh_seconds: int,
    ) -> None:
        await self.db.execute(
            """
            UPDATE user_containers
            SET terminal_columns = ?, terminal_rows = ?, terminal_idle_minutes = ?,
                terminal_rich_embed = ?, terminal_refresh_seconds = ?
            WHERE user_id = ?
            """,
            (
                columns,
                rows,
                idle_minutes,
                int(rich_embed),
                refresh_seconds,
                user_id,
            ),
        )
        await self.db.commit()

    async def set_terminal_message(
        self, user_id: int, message_id: int | None
    ) -> None:
        await self.db.execute(
            "UPDATE user_containers SET terminal_message_id = ? WHERE user_id = ?",
            (message_id, user_id),
        )
        await self.db.commit()

    async def delete_user_container(self, user_id: int, guild_id: int) -> None:
        await self.db.execute(
            "DELETE FROM user_containers WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        await self.db.commit()
