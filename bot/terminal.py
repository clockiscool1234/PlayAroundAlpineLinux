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
        self.screen = pyte.Screen(self.columns, self.rows)
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.connection: TerminalConnection | None = None
        self.message: discord.Message | None = None
        self.controls: discord.ui.View | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.render_task: asyncio.Task[None] | None = None
        self.write_lock = asyncio.Lock()
        self.message_lock = asyncio.Lock()
        self.alive = False
        self._closing = False
        self._dirty = False
        self._last_rendered: tuple[str, bool, int, int] | None = None

    def set_controls(self, controls: discord.ui.View) -> None:
        self.controls = controls
        self._last_rendered = None

    async def refresh(self) -> None:
        self._last_rendered = None
        await self._render()

    async def start(self) -> None:
        await self._ensure_message("Starting terminal…")
        self.connection = await self.bot.docker.open_terminal(
            self.record.container_id,
            columns=self.columns,
            rows=self.rows,
        )
        self.alive = True
        loop = asyncio.get_running_loop()
        self.reader_task = asyncio.create_task(
            asyncio.to_thread(self._blocking_reader, loop),
            name=f"terminal-reader-{self.record.user_id}",
        )
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
            self.message = await self.thread.send(
                embed=self.build_embed(initial=initial),
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
                data = self.connection.socket.read(4096)
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
            if not self._closing:
                log.warning("TTY reader failed for user %s: %s", self.record.user_id, exc)
        finally:
            if not self._closing:
                loop.call_soon_threadsafe(self._reader_finished)

    def _feed_output(self, data: bytes, consumed: threading.Event) -> None:
        try:
            if self._closing:
                return
            self.stream.feed(self.decoder.decode(data))
            self.request_render()
        finally:
            consumed.set()

    def _reader_finished(self) -> None:
        self.alive = False
        self.request_render()

    def request_render(self) -> None:
        self._dirty = True
        if self.render_task is None or self.render_task.done():
            self.render_task = asyncio.create_task(
                self._render_loop(),
                name=f"terminal-render-{self.record.user_id}",
            )

    async def _render_loop(self) -> None:
        # Coalesce bursts of TTY output to stay below Discord edit rate limits.
        await asyncio.sleep(0.4)
        while self._dirty and not self._closing:
            self._dirty = False
            await self._render()
            if self._dirty:
                await asyncio.sleep(0.7)

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

    def build_embed(self, *, initial: str = "") -> discord.Embed:
        terminal = self._screen_text(initial)
        if self.alive:
            colour = discord.Colour.green()
            status = "Connected"
        elif initial:
            colour = discord.Colour.gold()
            status = "Starting"
        else:
            colour = discord.Colour.dark_grey()
            status = "Disconnected — next input opens a new shell"

        embed = discord.Embed(
            title="Alpine Linux TTY",
            description=f"```\n{terminal}\n```",
            colour=colour,
        )
        embed.set_footer(
            text=(
                f"TTY display • {self.columns}×{self.rows} • {status} • "
                "Toggle keys, then press Send"
            )
        )
        return embed

    def _render_fingerprint(self) -> tuple[str, bool, int, int]:
        return (self._screen_text(), self.alive, self.columns, self.rows)

    async def _render(self) -> None:
        async with self.message_lock:
            await self._ensure_message()
            fingerprint = self._render_fingerprint()
            if fingerprint == self._last_rendered:
                return
            try:
                assert self.message is not None
                self.message = await self.message.edit(
                    content=None,
                    embed=self.build_embed(),
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
                if not is_current and not is_tty_embed:
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

    async def send_key_chord(self, key: str, *, ctrl: bool, alt: bool) -> None:
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

        await self.write(payload)

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
        self.request_render()

    async def close(self) -> None:
        self._closing = True
        self.alive = False
        if self.render_task and not self.render_task.done():
            self.render_task.cancel()
        if self.connection is not None:
            try:
                await asyncio.to_thread(self.connection.socket.close)
            except Exception:
                pass
        if self.reader_task and not self.reader_task.done():
            self.reader_task.cancel()
