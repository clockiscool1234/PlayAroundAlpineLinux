from __future__ import annotations

import logging

import discord
from discord.ext import commands

from .config import Config
from .db import Database
from .docker_manager import DockerManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("alpine-bot")


class AlpineBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        # Required to turn owner messages in a control thread into TTY input.
        intents.message_content = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )
        self.config = config
        self.db = Database(config.database_path)
        self.docker = DockerManager(
            image=config.container_image,
            memory=config.container_memory,
            disk=config.container_disk,
            cpus=config.container_cpus,
            pids_limit=config.container_pids_limit,
            network_mode=config.container_network,
            http_proxy=config.container_http_proxy,
            lxcfs_path=config.container_lxcfs_path,
            tmate=config.tmate,
        )

    async def setup_hook(self) -> None:
        await self.db.connect()
        try:
            await self.docker.ensure_image()
            await self.docker.ensure_network()
            await self.docker.ensure_disk_quota()
        except Exception as e:
            log.warning("Docker prerequisite check failed: %s", e)

        await self.load_extension("bot.cogs.container")

        if self.config.guild_id:
            guild = discord.Object(id=self.config.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced commands to guild %s", self.config.guild_id)
        else:
            await self.tree.sync()
            log.info("Synced commands globally")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user and self.user.id)

    async def close(self) -> None:
        if "bot.cogs.container" in self.extensions:
            await self.unload_extension("bot.cogs.container")
        await self.db.close()
        await super().close()


def run() -> None:
    config = Config.from_env()
    bot = AlpineBot(config)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    run()
