from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..db import UserContainer
from ..terminal import TerminalSession

log = logging.getLogger(__name__)


def _truncate(text: str, limit: int = 1900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n... (truncated)"


@dataclass
class PendingChord:
    ctrl: bool = False
    alt: bool = False
    key: str | None = None


class TerminalControlButton(discord.ui.Button["TerminalControlView"]):
    def __init__(
        self,
        *,
        action: str,
        label: str,
        row: int,
        selected: bool = False,
        send: bool = False,
    ) -> None:
        super().__init__(
            label=label,
            style=(
                discord.ButtonStyle.success
                if send
                else discord.ButtonStyle.primary
                if selected
                else discord.ButtonStyle.secondary
            ),
            custom_id=f"alpine-tty:{action}",
            row=row,
            disabled=send and not selected,
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.cog.handle_terminal_control(interaction, self.action)


class TerminalControlView(discord.ui.View):
    def __init__(
        self,
        cog: "ContainerControl",
        state: PendingChord | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        state = state or PendingChord()

        for action, label in (
            ("up", "↑"),
            ("down", "↓"),
            ("left", "←"),
            ("right", "→"),
        ):
            self.add_item(
                TerminalControlButton(
                    action=action,
                    label=label,
                    row=0,
                    selected=state.key == action,
                )
            )
        self.add_item(
            TerminalControlButton(
                action="ctrl", label="Ctrl", row=1, selected=state.ctrl
            )
        )
        self.add_item(
            TerminalControlButton(
                action="alt", label="Alt", row=1, selected=state.alt
            )
        )
        self.add_item(
            TerminalControlButton(
                action="escape",
                label="Esc",
                row=1,
                selected=state.key == "escape",
            )
        )
        self.add_item(
            TerminalControlButton(
                action="tab",
                label="Tab",
                row=1,
                selected=state.key == "tab",
            )
        )
        self.add_item(
            TerminalControlButton(
                action="send",
                label="Send",
                row=2,
                selected=state.key is not None,
                send=True,
            )
        )


class ContainerControl(commands.Cog):
    """Manage per-user Alpine Linux Docker containers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._creation_locks: dict[int, asyncio.Lock] = {}
        self._session_locks: dict[int, asyncio.Lock] = {}
        self._sessions: dict[int, TerminalSession] = {}
        self._control_states: dict[int, PendingChord] = {}
        # Handles component interactions after a bot restart, before a session
        # has recreated a message-specific view.
        self._persistent_view = TerminalControlView(self)
        self.bot.add_view(self._persistent_view)

    # ---- helpers ----

    async def _require_owner_in_thread(
        self, interaction: discord.Interaction
    ) -> Optional[UserContainer]:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return None
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "Use this command inside your container control thread.", ephemeral=True
            )
            return None
        rec = await self.bot.db.get_by_thread(interaction.channel.id)  # type: ignore[attr-defined]
        if rec is None:
            await interaction.response.send_message(
                "This thread is not linked to a managed container.", ephemeral=True
            )
            return None
        if rec.user_id != interaction.user.id:
            await interaction.response.send_message(
                "Only the owner of this container can control it.", ephemeral=True
            )
            return None
        return rec

    async def _ensure_terminal(
        self, record: UserContainer, thread: discord.Thread
    ) -> TerminalSession:
        lock = self._session_locks.setdefault(record.thread_id, asyncio.Lock())
        async with lock:
            current = self._sessions.get(record.thread_id)
            if current and current.alive:
                return current
            if current:
                await current.close()

            session = TerminalSession(self.bot, record, thread)  # type: ignore[arg-type]
            session.set_controls(self._control_view(record.thread_id))
            self._sessions[record.thread_id] = session
            try:
                await session.start()
            except Exception:
                self._sessions.pop(record.thread_id, None)
                await session.close()
                raise
            return session

    async def _close_terminal(self, thread_id: int) -> None:
        lock = self._session_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            session = self._sessions.pop(thread_id, None)
            if session:
                await session.close()

    def _control_view(self, thread_id: int) -> TerminalControlView:
        state = self._control_states.setdefault(thread_id, PendingChord())
        return TerminalControlView(self, state)

    async def handle_terminal_control(
        self, interaction: discord.Interaction, action: str
    ) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "This terminal control is no longer linked to a thread.",
                ephemeral=True,
            )
            return

        record = await self.bot.db.get_by_thread(interaction.channel.id)  # type: ignore[attr-defined]
        if (
            record is None
            or record.user_id != interaction.user.id
            or interaction.message is None
            or record.terminal_message_id != interaction.message.id
        ):
            await interaction.response.send_message(
                "Only the owner can use this terminal's controls.",
                ephemeral=True,
            )
            return

        state = self._control_states.setdefault(record.thread_id, PendingChord())
        if action == "send" and state.key is None:
            await interaction.response.send_message(
                "Select an arrow, Esc, or Tab first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        try:
            session = await self._ensure_terminal(record, interaction.channel)
            if action == "ctrl":
                state.ctrl = not state.ctrl
            elif action == "alt":
                state.alt = not state.alt
            elif action in ("up", "down", "left", "right", "escape", "tab"):
                state.key = None if state.key == action else action
            elif action == "send":
                assert state.key is not None
                await session.send_key_chord(
                    state.key,
                    ctrl=state.ctrl,
                    alt=state.alt,
                )
                state.ctrl = False
                state.alt = False
                state.key = None
            else:
                raise ValueError(f"Unknown terminal control: {action}")

            session.set_controls(self._control_view(record.thread_id))
            await session.refresh()
        except Exception as e:
            log.exception("Terminal component failed for user %s", record.user_id)
            await interaction.followup.send(
                f"Terminal control failed: `{_truncate(str(e), 1200)}`",
                ephemeral=True,
            )

    async def cog_unload(self) -> None:
        self._persistent_view.stop()
        sessions = list(self._sessions.values())
        self._sessions.clear()
        self._control_states.clear()
        await asyncio.gather(
            *(session.close() for session in sessions),
            return_exceptions=True,
        )

    # ---- commands ----

    @app_commands.command(
        name="givcontainerpls",
        description="Create your personal Alpine Linux container and a control thread.",
    )
    @app_commands.guild_only()
    async def givcontainerpls(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        user = interaction.user
        channel = interaction.channel

        if channel is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Please run this command in a regular text channel.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        lock = self._creation_locks.setdefault(user.id, asyncio.Lock())
        async with lock:
            existing = await self.bot.db.get_user_container(user.id)  # type: ignore[attr-defined]
            if existing:
                await interaction.followup.send(
                    f"You already have a container (the one-container limit is global).\n"
                    f"Control thread: <#{existing.thread_id}>\n"
                    f"Status: `{existing.status}`\n"
                    f"Use `/destroycontainer` in that thread if you want to remove it first.",
                    ephemeral=True,
                )
                return

            await self._create_for_user(interaction, channel)

    async def _create_for_user(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        """Create a thread and container while the caller holds the per-user lock."""
        assert interaction.guild_id is not None
        user = interaction.user

        # Create thread first so we have a place to report progress.
        thread_name = f"alpine-{user.name}"[:90]
        thread: Optional[discord.Thread] = None
        try:
            thread = await channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False,
                auto_archive_duration=channel.default_auto_archive_duration,
                reason=f"Alpine container control thread for {user} ({user.id})",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "I don't have permission to create threads in this channel.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"Failed to create thread: {e}", ephemeral=True)
            return

        try:
            await thread.add_user(user)
        except Exception:
            await interaction.followup.send(
                "The control thread was created, but I could not add you to it. "
                "Please ask an administrator to check my thread permissions.",
                ephemeral=True,
            )
            try:
                await thread.delete(reason="Could not add container owner")
            except Exception:
                pass
            return

        await thread.send(
            f"{user.mention} creating your Alpine Linux container...\n"
            f"- Image: `{self.bot.config.container_image}`\n"  # type: ignore[attr-defined]
            f"- Memory: `{self.bot.config.container_memory}`\n"  # type: ignore[attr-defined]
            f"- Disk: `{self.bot.config.container_disk}` (hard writable-layer quota)\n"  # type: ignore[attr-defined]
            f"- CPUs: `{self.bot.config.container_cpus}`\n"  # type: ignore[attr-defined]
            f"- Network: `{self.bot.config.container_network}`\n"  # type: ignore[attr-defined]
            f"- PIDs limit: `{self.bot.config.container_pids_limit}`"  # type: ignore[attr-defined]
        )

        try:
            container = await self.bot.docker.create_container(interaction.guild_id, user.id)  # type: ignore[attr-defined]
        except Exception as e:
            log.exception("Failed to create container for %s", user.id)
            error = _truncate(str(e).replace("`", "'"), 1500)
            await thread.send(f"Failed to create container: `{error}`")
            await interaction.followup.send(
                f"Failed to create container. See {thread.mention} for details.",
                ephemeral=True,
            )
            try:
                await thread.edit(archived=True, locked=True)
            except Exception:
                pass
            return

        try:
            record = await self.bot.db.create_user_container(  # type: ignore[attr-defined]
                user_id=user.id,
                guild_id=interaction.guild_id,
                thread_id=thread.id,
                container_id=container.id,
                container_name=container.name or "",
                status="running",
            )
        except Exception as e:
            log.exception("Failed to save container record, cleaning up")
            try:
                await self.bot.docker.remove_container(container.id)  # type: ignore[attr-defined]
            except Exception:
                pass
            await thread.send(f"Failed to save container record: `{e}`. Container removed.")
            await interaction.followup.send("Failed to save container record.", ephemeral=True)
            return

        await thread.send(
            "Your container is ready.\n"
            "Send a normal message in this thread to type it into the terminal. "
            "Your input message will be deleted and the terminal display below will update.\n\n"
            "Commands you can use:\n"
            "- `/settings` - view or change terminal dimensions\n"
            "- `/printtty` - move the current terminal display to the bottom\n"
            "- `/status` - show container status\n"
            "- `/stop` - stop the container\n"
            "- `/start` - start the container\n"
            "- `/destroycontainer` - permanently delete the container and its data\n\n"
            "Use the buttons below the terminal to select arrows, Ctrl, Alt, "
            "Esc, or Tab, then press **Send** to transmit the combination.\n\n"
            "The terminal runs as root in `/root` and stays up until you stop/destroy it."
        )
        try:
            await self._ensure_terminal(record, thread)
        except Exception as e:
            log.exception("Failed to open initial terminal for user %s", user.id)
            await thread.send(
                f"The container is running, but its terminal could not be opened: "
                f"`{_truncate(str(e), 1200)}`"
            )
        await interaction.followup.send(
            f"Container created. Control it in {thread.mention}.", ephemeral=True
        )

    @app_commands.command(
        name="settings",
        description="View or change your terminal dimensions.",
    )
    @app_commands.describe(
        columns="Terminal width (20-120)",
        rows="Terminal height (5-40)",
    )
    @app_commands.guild_only()
    async def settings(
        self,
        interaction: discord.Interaction,
        columns: Optional[app_commands.Range[int, 20, 120]] = None,
        rows: Optional[app_commands.Range[int, 5, 40]] = None,
    ) -> None:
        rec = await self._require_owner_in_thread(interaction)
        if rec is None:
            return

        new_columns = columns if columns is not None else rec.terminal_columns
        new_rows = rows if rows is not None else rec.terminal_rows
        if new_columns * new_rows > 1760:
            await interaction.response.send_message(
                "That terminal is too large for one Discord message. "
                "Choose dimensions whose product is at most 1760.",
                ephemeral=True,
            )
            return

        if columns is None and rows is None:
            await interaction.response.send_message(
                f"Terminal size: `{rec.terminal_columns}×{rec.terminal_rows}`",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        await self.bot.db.update_terminal_settings(  # type: ignore[attr-defined]
            rec.user_id,
            columns=new_columns,
            rows=new_rows,
        )
        rec.terminal_columns = new_columns
        rec.terminal_rows = new_rows
        session = self._sessions.get(rec.thread_id)
        if session:
            try:
                await session.resize(columns=new_columns, rows=new_rows)
            except Exception as e:
                log.exception("Could not resize active terminal for user %s", rec.user_id)
                await interaction.followup.send(
                    f"The `{new_columns}×{new_rows}` setting was saved, but the active "
                    f"terminal could not be resized: `{_truncate(str(e), 1000)}`. "
                    "It will apply when the shell reconnects.",
                    ephemeral=True,
                )
                return
        await interaction.followup.send(
            f"Terminal resized to `{new_columns}×{new_rows}`.",
            ephemeral=True,
        )

    @app_commands.command(
        name="printtty",
        description="Move the live terminal display to the bottom of this thread.",
    )
    @app_commands.guild_only()
    async def printtty(self, interaction: discord.Interaction) -> None:
        rec = await self._require_owner_in_thread(interaction)
        if rec is None:
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        assert isinstance(interaction.channel, discord.Thread)
        try:
            session = await self._ensure_terminal(rec, interaction.channel)
            session.set_controls(self._control_view(rec.thread_id))
            await session.repost()
        except Exception as e:
            log.exception("Could not repost terminal for user %s", rec.user_id)
            await interaction.followup.send(
                f"Could not repost the terminal: `{_truncate(str(e), 1200)}`",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "Terminal moved to the bottom of the thread.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not isinstance(message.channel, discord.Thread):
            return

        rec = await self.bot.db.get_by_thread(message.channel.id)  # type: ignore[attr-defined]
        if rec is None or rec.user_id != message.author.id:
            return

        try:
            await message.delete()
        except discord.Forbidden:
            await message.channel.send(
                "I need **Manage Messages** permission to securely forward terminal input. "
                "That message was not executed."
            )
            return
        except discord.HTTPException:
            return

        if not message.content:
            return

        try:
            session = await self._ensure_terminal(rec, message.channel)
            await session.send_message_input(message.content)
        except Exception as e:
            log.exception("Could not send TTY input for user %s", rec.user_id)
            await message.channel.send(
                f"Input was not executed because the terminal is unavailable: "
                f"`{_truncate(str(e), 1200)}`"
            )

    @app_commands.command(name="status", description="Show your container status (use in your control thread).")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        rec = await self._require_owner_in_thread(interaction)
        if rec is None:
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        status = await self.bot.docker.container_status(rec.container_id)  # type: ignore[attr-defined]
        if status is None:
            status = "missing"
        await self.bot.db.update_status(rec.user_id, rec.guild_id, status)  # type: ignore[attr-defined]
        await interaction.followup.send(
            f"Container: `{rec.container_name or rec.container_id[:12]}`\n"
            f"ID: `{rec.container_id[:12]}`\n"
            f"Status: `{status}`",
            ephemeral=True,
        )

    @app_commands.command(name="start", description="Start your container (use in your control thread).")
    @app_commands.guild_only()
    async def start(self, interaction: discord.Interaction) -> None:
        rec = await self._require_owner_in_thread(interaction)
        if rec is None:
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        ok = await self.bot.docker.start_container(rec.container_id)  # type: ignore[attr-defined]
        if not ok:
            await self.bot.db.update_status(rec.user_id, rec.guild_id, "missing")  # type: ignore[attr-defined]
            await interaction.followup.send("Container not found.", ephemeral=True)
            return
        await self.bot.db.update_status(rec.user_id, rec.guild_id, "running")  # type: ignore[attr-defined]
        assert isinstance(interaction.channel, discord.Thread)
        try:
            await self._ensure_terminal(rec, interaction.channel)
        except Exception as e:
            await interaction.followup.send(
                f"Container started, but the terminal could not be opened: "
                f"`{_truncate(str(e), 1200)}`",
                ephemeral=True,
            )
            return
        await interaction.followup.send("Container started.", ephemeral=True)

    @app_commands.command(name="stop", description="Stop your container (use in your control thread).")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        rec = await self._require_owner_in_thread(interaction)
        if rec is None:
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        await self._close_terminal(rec.thread_id)
        ok = await self.bot.docker.stop_container(rec.container_id)  # type: ignore[attr-defined]
        if not ok:
            await self.bot.db.update_status(rec.user_id, rec.guild_id, "missing")  # type: ignore[attr-defined]
            await interaction.followup.send("Container not found.", ephemeral=True)
            return
        await self.bot.db.update_status(rec.user_id, rec.guild_id, "exited")  # type: ignore[attr-defined]
        await interaction.followup.send("Container stopped.", ephemeral=True)

    @app_commands.command(
        name="destroycontainer",
        description="Permanently delete your container and its data (use in your control thread).",
    )
    @app_commands.guild_only()
    async def destroycontainer(self, interaction: discord.Interaction) -> None:
        rec = await self._require_owner_in_thread(interaction)
        if rec is None:
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        await self._close_terminal(rec.thread_id)
        try:
            await self.bot.docker.remove_container(rec.container_id)  # type: ignore[attr-defined]
        except Exception as e:
            log.exception("Error removing container %s", rec.container_id)
            await interaction.followup.send(
                f"Could not destroy the container: `{_truncate(str(e), 1200)}`. "
                "The database record was kept; please try again or contact an administrator.",
                ephemeral=True,
            )
            return

        await self.bot.db.delete_user_container(rec.user_id, rec.guild_id)  # type: ignore[attr-defined]
        self._control_states.pop(rec.thread_id, None)

        await interaction.followup.send("Container destroyed. You can create a new one with /givcontainerpls.", ephemeral=True)
        try:
            if isinstance(interaction.channel, discord.Thread):
                await interaction.channel.send(
                    f"Container destroyed by {interaction.user.mention}. This thread is no longer linked to a container."
                )
                await interaction.channel.edit(archived=True, locked=True)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ContainerControl(bot))
