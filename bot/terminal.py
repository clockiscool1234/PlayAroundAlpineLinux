from __future__ import annotations

import asyncio
import codecs
import logging
import threading
import time
from typing import TYPE_CHECKING

import discord
import pyte

from .db import UserContainer
from .docker_manager import TerminalConnection

if TYPE_CHECKING:
    from .main import AlpineBot

log = logging.getLogger(__name__)

MAX_TTY_BYTES_PER_SECOND = 64 * 1024
KEY_BYTES = {
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "escape": b"\x1b",
    "tab": b"\t",
}


class TerminalSession:
    """One persistent Docker exec TTY rendered into one Discord embed."""

    def __init__(
        self,
        bot: AlpineBot,
        record: UserContainer,
        thread: discord.Thread,
    ) -> None:
        self.bot = bot
        self.record = record
        self.thread = thread
        self.columns = record.terminal_columns
        self.rows = record.terminal_rows
        self.idle_minutes = record.terminal_idle_minutes
        self.rich_embed = bool(record.terminal_rich_embed)
        self.refresh_seconds = record.terminal_refresh_seconds
        self.screen = pyte.Screen(self.columns, self.rows)
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.connection: TerminalConnection | None = None
        self.message: discord.Message | None = None
        self.controls: discord.ui.View | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.render_task: asyncio.Task[None] | None = None
        self.idle_task: asyncio.Task[None] | None = None
        self.write_lock = asyncio.Lock()
        self.message_lock = asyncio.Lock()
        self.alive = False
        self.visible = True
        self._closing = False
        self._expected_disconnect = False
        self._idle_expired = False
        self._last_activity = time.monotonic()
        self._dirty = False
        self._last_rendered: tuple[
            str, bool, int, int, int, bool, bool, int
        ] | None = None

    def set_controls(self, controls: discord.ui.View) -> None:
        self.controls = controls
        self._last_rendered = None

    async def refresh(self) -> None:
        if not self.visible:
            return
        self._last_rendered = None
        await self._render()

    async def hide_message(self) -> None:
        self.visible = False
        if self.render_task and not self.render_task.done():
            self.render_task.cancel()
        self.render_task = None
        async with self.message_lock:
            if self.message is not None:
                try:
                    await self.message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            self.message = None
            self.record.terminal_message_id = None
            self._last_rendered = None
            await self.bot.db.set_terminal_message(self.record.user_id, None)

    async def show_message(self) -> None:
        self.visible = True
        self._dirty = False
        self._last_rendered = None
        await self._ensure_message()
        await self._render()

    async def start(self) -> None:
        await self._ensure_message("Starting terminal…")
        self.connection = await self.bot.docker.open_terminal(
            self.record.container_id,
            columns=self.columns,
            rows=self.rows,
        )
        self.alive = True
        self._expected_disconnect = False
        self._idle_expired = False
        self.touch_activity()
        loop = asyncio.get_running_loop()
        self.reader_task = asyncio.create_task(
            asyncio.to_thread(self._blocking_reader, loop),
            name=f"terminal-reader-{self.record.user_id}",
        )
        self._start_idle_task()
        self.request_render()

    async def _ensure_message(self, initial: str = "") -> None:
        if self.message is not None:
            return

        fetched_existing = False
        if self.record.terminal_message_id:
            try:
                self.message = await self.thread.fetch_message(
                    self.record.terminal_message_id
                )
                fetched_existing = True
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                self.message = None

        if self.message is None:
            if self.rich_embed:
                self.message = await self.thread.send(
                    embed=self.build_embed(initial=initial),
                    view=self.controls,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                self.message = await self.thread.send(
                    self.build_plain_content(initial=initial),
                    view=self.controls,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            self.record.terminal_message_id = self.message.id
            await self.bot.db.set_terminal_message(
                self.record.user_id, self.message.id
            )
        elif fetched_existing:
            # Remove controls from the previous reaction-based implementation.
            try:
                await self.message.clear_reactions()
            except discord.HTTPException:
                pass

    def _blocking_reader(self, loop: asyncio.AbstractEventLoop) -> None:
        assert self.connection is not None
        window_started = time.monotonic()
        window_bytes = 0
        try:
            while not self._closing:
                try:
                    data = self.connection.socket.read(4096)
                except TimeoutError:
                    # Some Docker transports retain an HTTP read timeout even
                    # after settimeout(None). A quiet TTY is not disconnected.
                    continue
                if not data:
                    break

                # Backpressure prevents programs such as `yes` from flooding
                # the event loop faster than Discord can render their output.
                now = time.monotonic()
                if now - window_started >= 1.0:
                    window_started = now
                    window_bytes = 0
                window_bytes += len(data)
                if window_bytes > MAX_TTY_BYTES_PER_SECOND:
                    time.sleep(max(0.0, 1.0 - (now - window_started)))
                    window_started = time.monotonic()
                    window_bytes = len(data)

                consumed = threading.Event()
                loop.call_soon_threadsafe(self._feed_output, data, consumed)
                consumed.wait()
        except Exception as exc:
            if not self._closing and not self._expected_disconnect:
                log.warning("TTY reader failed for user %s: %s", self.record.user_id, exc)
        finally:
            if not self._closing:
                loop.call_soon_threadsafe(self._reader_finished)

    def _feed_output(self, data: bytes, consumed: threading.Event) -> None:
        try:
            if self._closing:
                return
            self.stream.feed(self.decoder.decode(data))
            self.touch_activity()
            self.request_render()
        finally:
            consumed.set()

    def _reader_finished(self) -> None:
        self.alive = False
        if (
            not self._idle_expired
            and self.idle_task
            and not self.idle_task.done()
        ):
            self.idle_task.cancel()
        self.request_render()

    def touch_activity(self) -> None:
        self._last_activity = time.monotonic()

    def _start_idle_task(self) -> None:
        if self.idle_task and not self.idle_task.done():
            self.idle_task.cancel()
        if self.alive and self.idle_minutes > 0:
            self.idle_task = asyncio.create_task(
                self._idle_watchdog(),
                name=f"terminal-idle-{self.record.user_id}",
            )
        else:
            self.idle_task = None

    async def _idle_watchdog(self) -> None:
        timeout = self.idle_minutes * 60
        while self.alive and not self._closing and timeout > 0:
            remaining = self._last_activity + timeout - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue

            log.info(
                "Closing idle TTY for user %s after %s minute(s)",
                self.record.user_id,
                self.idle_minutes,
            )
            self._expected_disconnect = True
            self._idle_expired = True
            self.alive = False
            await self._close_connection_socket()
            self.request_render()
            return

    def request_render(self) -> None:
        self._dirty = True
        if not self.visible:
            return
        if self.render_task is None or self.render_task.done():
            self.render_task = asyncio.create_task(
                self._render_loop(),
                name=f"terminal-render-{self.record.user_id}",
            )

    async def _render_loop(self) -> None:
        # Coalesce bursts of TTY output to stay below Discord edit rate limits.
        await asyncio.sleep(self.refresh_seconds)
        while self._dirty and not self._closing:
            self._dirty = False
            await self._render()
            if self._dirty:
                await asyncio.sleep(self.refresh_seconds)

    def _screen_text(self, initial: str = "") -> str:
        if initial:
            lines = [initial[: self.columns].ljust(self.columns)]
            lines.extend(" " * self.columns for _ in range(self.rows - 1))
        else:
            lines = []
            for line in list(self.screen.display)[: self.rows]:
                lines.append(line[: self.columns].ljust(self.columns))
            lines.extend(" " * self.columns for _ in range(self.rows - len(lines)))

        # Prevent terminal output from closing Discord's Markdown code fence.
        return "\n".join(lines).replace("`", "ˋ")

    def _status_text(self, *, initial: str = "") -> tuple[discord.Colour, str]:
        if self.alive:
            colour = discord.Colour.green()
            status = "Connected"
        elif initial:
            colour = discord.Colour.gold()
            status = "Starting"
        else:
            colour = discord.Colour.dark_grey()
            status = (
                "Idle timeout — next input opens a new shell"
                if self._idle_expired
                else "Disconnected — next input opens a new shell"
            )
        return colour, status

    def _display_footer(self, *, initial: str = "") -> str:
        _, status = self._status_text(initial=initial)
        idle = "off" if self.idle_minutes == 0 else f"{self.idle_minutes}m"
        return (
            f"TTY display • {self.columns}×{self.rows} • {status} • "
            f"Idle: {idle} • Refresh: {self.refresh_seconds}s"
        )

    def build_embed(self, *, initial: str = "") -> discord.Embed:
        terminal = self._screen_text(initial)
        colour, _ = self._status_text(initial=initial)

        embed = discord.Embed(
            title="Alpine Linux TTY",
            description=f"```\n{terminal}\n```",
            colour=colour,
        )
        embed.set_footer(text=self._display_footer(initial=initial))
        return embed

    def build_plain_content(self, *, initial: str = "") -> str:
        return (
            f"```\n{self._screen_text(initial)}\n```\n"
            f"`{self._display_footer(initial=initial)}`"
        )

    def _render_fingerprint(
        self,
    ) -> tuple[str, bool, int, int, int, bool, bool, int]:
        return (
            self._screen_text(),
            self.alive,
            self.columns,
            self.rows,
            self.idle_minutes,
            self._idle_expired,
            self.rich_embed,
            self.refresh_seconds,
        )

    async def _render(self) -> None:
        if not self.visible:
            return
        async with self.message_lock:
            await self._ensure_message()
            fingerprint = self._render_fingerprint()
            if fingerprint == self._last_rendered:
                return
            try:
                assert self.message is not None
                if self.rich_embed:
                    self.message = await self.message.edit(
                        content=None,
                        embed=self.build_embed(),
                        view=self.controls,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    self.message = await self.message.edit(
                        content=self.build_plain_content(),
                        embed=None,
                        view=self.controls,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                self._last_rendered = fingerprint
            except discord.NotFound:
                self.message = None
                self.record.terminal_message_id = None
                await self.bot.db.set_terminal_message(self.record.user_id, None)
                await self._ensure_message()

    async def repost(self) -> None:
        """Delete old TTY embeds and post the live display at the thread bottom."""
        async with self.message_lock:
            async for message in self.thread.history(limit=None):
                if self.bot.user is None or message.author.id != self.bot.user.id:
                    continue
                is_current = self.message is not None and message.id == self.message.id
                is_tty_embed = any(
                    embed.footer.text
                    and embed.footer.text.startswith("TTY display •")
                    for embed in message.embeds
                )
                is_tty_text = bool(
                    message.content and "`TTY display •" in message.content
                )
                if not is_current and not is_tty_embed and not is_tty_text:
                    continue
                try:
                    await message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

            self.message = None
            self.record.terminal_message_id = None
            self._last_rendered = None
            await self.bot.db.set_terminal_message(self.record.user_id, None)
            await self._ensure_message()
            self._last_rendered = self._render_fingerprint()

    async def send_message_input(self, text: str) -> None:
        await self.write(text.encode("utf-8", errors="replace") + b"\n")

    @staticmethod
    def key_chord_bytes(key: str, *, ctrl: bool, alt: bool) -> bytes:
        if key not in KEY_BYTES:
            raise ValueError(f"Unknown terminal key: {key}")

        payload = KEY_BYTES[key]
        if key in ("up", "down", "left", "right"):
            suffix = payload[-1:]
            if ctrl and alt:
                payload = b"\x1b[1;7" + suffix
            elif ctrl:
                payload = b"\x1b[1;5" + suffix
            elif alt:
                payload = b"\x1b[1;3" + suffix
        elif key == "tab":
            if ctrl and alt:
                payload = b"\x1b[1;7I"
            elif ctrl:
                payload = b"\x1b[1;5I"
            elif alt:
                payload = b"\x1b\t"
        elif key == "escape" and alt:
            payload = b"\x1b\x1b"

        return payload

    @staticmethod
    def modified_text_bytes(text: str, *, ctrl: bool, alt: bool) -> bytes:
        if not text:
            return b""

        first = text[0]
        if ctrl and ord(first) < 128:
            first_bytes = bytes((ord(first.upper()) & 0x1F,))
        else:
            first_bytes = first.encode("utf-8", errors="replace")
        payload = first_bytes + text[1:].encode("utf-8", errors="replace")
        return b"\x1b" + payload if alt else payload

    async def send_key_chord(self, key: str, *, ctrl: bool, alt: bool) -> None:
        await self.write(self.key_chord_bytes(key, ctrl=ctrl, alt=alt))

    async def send_pending_text(
        self,
        text: str,
        *,
        key: str | None,
        ctrl: bool,
        alt: bool,
    ) -> None:
        payload = b""
        if key is not None:
            payload += self.key_chord_bytes(key, ctrl=ctrl, alt=alt)
            ctrl = False
            alt = False
        payload += self.modified_text_bytes(text, ctrl=ctrl, alt=alt)
        await self.write(payload)

    async def send_key_sequence(self, expression: str) -> None:
        tokens = [token.strip() for token in expression.split("+")]
        tokens = [token for token in tokens if token]
        if not tokens:
            raise ValueError("Key sequence cannot be empty")

        payload = b""
        ctrl = False
        alt = False
        sent_key = False
        aliases = {"esc": "escape"}
        for token in tokens:
            token_name = token.lower()
            if token_name == "ctrl":
                ctrl = True
                continue
            if token_name == "alt":
                alt = True
                continue

            key = aliases.get(token_name, token_name)
            if key in KEY_BYTES:
                payload += self.key_chord_bytes(key, ctrl=ctrl, alt=alt)
            else:
                payload += self.modified_text_bytes(token, ctrl=ctrl, alt=alt)
            ctrl = False
            alt = False
            sent_key = True

        if ctrl or alt or not sent_key:
            raise ValueError("Ctrl or Alt must be followed by a letter or special key")
        await self.write(payload)

    async def reset_terminal(self) -> None:
        self.screen.reset()
        self._last_rendered = None
        await self.write(b"reset\n")
        await self.refresh()

    async def write(self, data: bytes) -> None:
        if not self.alive or self.connection is None:
            raise RuntimeError("Terminal is not connected")

        async with self.write_lock:
            socket = self.connection.socket

            def _write_all() -> None:
                # Docker's UNIX transport returns a read-mode SocketIO wrapper.
                # Its public write() raises "File or stream is not writable",
                # while the underlying socket is the bidirectional hijacked
                # Docker exec connection.
                transport = getattr(socket, "_sock", socket)
                if hasattr(transport, "sendall"):
                    transport.sendall(data)
                    return

                remaining = memoryview(data)
                writer = getattr(transport, "send", None) or getattr(
                    transport, "write", None
                )
                if writer is None:
                    raise RuntimeError("Docker terminal socket is not writable")
                while remaining:
                    written = writer(remaining)
                    if written is None:
                        return
                    if written <= 0:
                        raise RuntimeError("Terminal socket closed while writing")
                    remaining = remaining[written:]

            await asyncio.to_thread(_write_all)
            self.touch_activity()

    async def resize(self, *, columns: int, rows: int) -> None:
        self.columns = columns
        self.rows = rows
        self.record.terminal_columns = columns
        self.record.terminal_rows = rows
        self.screen.resize(lines=rows, columns=columns)
        if self.connection and self.alive:
            await self.bot.docker.resize_terminal(
                self.connection.exec_id,
                columns=columns,
                rows=rows,
            )
        self._last_rendered = None
        self.touch_activity()
        self.request_render()

    def update_idle_timeout(self, minutes: int) -> None:
        self.idle_minutes = minutes
        self.record.terminal_idle_minutes = minutes
        self.touch_activity()
        self._start_idle_task()
        self._last_rendered = None
        self.request_render()

    def update_display_settings(
        self, *, rich_embed: bool, refresh_seconds: int
    ) -> None:
        self.rich_embed = rich_embed
        self.refresh_seconds = refresh_seconds
        self.record.terminal_rich_embed = int(rich_embed)
        self.record.terminal_refresh_seconds = refresh_seconds
        if self.render_task and not self.render_task.done():
            self.render_task.cancel()
        self.render_task = None
        self._last_rendered = None
        self.request_render()

    async def _close_connection_socket(self) -> None:
        if self.connection is None:
            return

        socket = self.connection.socket

        def _close() -> None:
            transport = getattr(socket, "_sock", socket)
            shutdown = getattr(transport, "shutdown", None)
            if shutdown is not None:
                try:
                    shutdown(2)
                except Exception:
                    pass
            socket.close()

        try:
            await asyncio.to_thread(_close)
        except Exception:
            pass

    async def close(self) -> None:
        self._closing = True
        self.alive = False
        self._expected_disconnect = True
        if self.render_task and not self.render_task.done():
            self.render_task.cancel()
        if self.idle_task and not self.idle_task.done():
            self.idle_task.cancel()
        await self._close_connection_socket()
        if self.reader_task and not self.reader_task.done():
            self.reader_task.cancel()
