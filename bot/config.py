from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
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
class UserSettingsDefaults:
    columns: int = 80
    rows: int = 22
    idle_minutes: int = 10
    rich_embed: bool = True
    refresh_seconds: int = 3

    @classmethod
    def from_toml(cls, path: Path) -> "UserSettingsDefaults":
        try:
            with path.open("rb") as file:
                document = tomllib.load(file)
        except FileNotFoundError as error:
            raise RuntimeError(
                f"User defaults file not found: {path}. "
                "Set USER_DEFAULTS_PATH or restore config/user-defaults.toml."
            ) from error
        except tomllib.TOMLDecodeError as error:
            raise RuntimeError(f"Invalid TOML in user defaults file {path}: {error}") from error

        values = document.get("terminal")
        if not isinstance(values, dict):
            raise RuntimeError(
                f"User defaults file {path} must contain a [terminal] table"
            )

        expected = {
            "columns",
            "rows",
            "idle_minutes",
            "rich_embed",
            "refresh_seconds",
        }
        unknown = set(values) - expected
        if unknown:
            raise RuntimeError(
                f"Unknown terminal default(s) in {path}: {', '.join(sorted(unknown))}"
            )

        defaults = cls(
            columns=_toml_int(values, "columns", 80),
            rows=_toml_int(values, "rows", 22),
            idle_minutes=_toml_int(values, "idle_minutes", 10),
            rich_embed=_toml_bool(values, "rich_embed", True),
            refresh_seconds=_toml_int(values, "refresh_seconds", 3),
        )
        defaults._validate(path)
        return defaults

    def _validate(self, path: Path) -> None:
        ranges = {
            "columns": (self.columns, 20, 120),
            "rows": (self.rows, 5, 40),
            "idle_minutes": (self.idle_minutes, 0, 1440),
            "refresh_seconds": (self.refresh_seconds, 1, 30),
        }
        for name, (value, minimum, maximum) in ranges.items():
            if not minimum <= value <= maximum:
                raise RuntimeError(
                    f"terminal.{name} in {path} must be between "
                    f"{minimum} and {maximum}"
                )
        if self.columns * self.rows > 1760:
            raise RuntimeError(
                f"Terminal defaults in {path} are too large for Discord: "
                "columns × rows must be at most 1760"
            )


def _toml_int(values: dict[str, object], name: str, default: int) -> int:
    value = values.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"terminal.{name} must be an integer")
    return value


def _toml_bool(values: dict[str, object], name: str, default: bool) -> bool:
    value = values.get(name, default)
    if not isinstance(value, bool):
        raise RuntimeError(f"terminal.{name} must be true or false")
    return value


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
    user_defaults: UserSettingsDefaults = field(default_factory=UserSettingsDefaults)

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
        defaults_path = Path(
            os.getenv("USER_DEFAULTS_PATH", "config/user-defaults.toml")
        )

        return cls(
            discord_token=token,
            guild_id=guild_id,
            container_image=os.getenv(
                "CONTAINER_IMAGE", "playaround-alpine-xorg:3.20"
            ).strip(),
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
            user_defaults=UserSettingsDefaults.from_toml(defaults_path),
        )
