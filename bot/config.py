from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int | None
    container_image: str
    container_memory: str
    container_disk: str
    container_cpus: float
    container_pids_limit: int
    container_network: str
    container_http_proxy: str
    database_path: Path

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token or token == "your-bot-token-here":
            raise RuntimeError(
                "DISCORD_TOKEN is not set. Copy .env.example to .env and set a valid bot token."
            )

        guild_raw = os.getenv("GUILD_ID", "").strip()
        guild_id = int(guild_raw) if guild_raw else None

        db_path = Path(os.getenv("DATABASE_PATH", "data/bot.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)

        return cls(
            discord_token=token,
            guild_id=guild_id,
            container_image=os.getenv("CONTAINER_IMAGE", "alpine:3.20").strip(),
            container_memory=os.getenv("CONTAINER_MEMORY", "512m").strip(),
            container_disk=os.getenv("CONTAINER_DISK", "1g").strip(),
            container_cpus=_get_float("CONTAINER_CPUS", 1.0),
            container_pids_limit=_get_int("CONTAINER_PIDS_LIMIT", 128),
            container_network=os.getenv(
                "CONTAINER_NETWORK", "alpine-filtered"
            ).strip(),
            container_http_proxy=os.getenv(
                "CONTAINER_HTTP_PROXY", "http://172.30.0.2:3128"
            ).strip(),
            database_path=db_path,
        )
