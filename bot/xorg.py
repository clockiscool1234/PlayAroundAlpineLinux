from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from .db import UserContainer

if TYPE_CHECKING:
    from .main import AlpineBot


@dataclass
class XorgControlState:
    step: int = 10
    ctrl: bool = False
    alt: bool = False
    left_down: bool = False
    right_down: bool = False


class XorgControlButton(discord.ui.Button["XorgControlView"]):
    def __init__(
        self,
        *,
        action: str,
        label: str,
        row: int,
        selected: bool = False,
        primary: bool = False,
    ) -> None:
        super().__init__(
            label=label,
            style=(
                discord.ButtonStyle.primary
                if selected or primary
                else discord.ButtonStyle.secondary
            ),
            custom_id=f"alpine-xorg:{action}",
            row=row,
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.cog.handle_xorg_control(interaction, self.action)


class XorgControlView(discord.ui.View):
    def __init__(self, cog: object, state: XorgControlState | None = None) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        state = state or XorgControlState()

        for action, label in (
            ("up", "↑"),
            ("down", "↓"),
            ("left", "←"),
            ("right", "→"),
        ):
            self.add_item(
                XorgControlButton(action=action, label=label, row=0)
            )

        for step in (100, 10, 1):
            self.add_item(
                XorgControlButton(
                    action=f"step-{step}",
                    label=f"{step} px",
                    row=1,
                    selected=state.step == step,
                )
            )
        self.add_item(
            XorgControlButton(
                action="refresh", label="Refresh", row=1, primary=True
            )
        )

        self.add_item(
            XorgControlButton(
                action="left-click",
                label="Left down" if state.left_down else "Left click",
                row=2,
                selected=state.left_down,
            )
        )
        self.add_item(
            XorgControlButton(
                action="right-click",
                label="Right down" if state.right_down else "Right click",
                row=2,
                selected=state.right_down,
            )
        )
        self.add_item(
            XorgControlButton(
                action="ctrl", label="Ctrl", row=2, selected=state.ctrl
            )
        )
        self.add_item(
            XorgControlButton(
                action="alt", label="Alt", row=2, selected=state.alt
            )
        )

        for action, label in (
            ("escape", "Esc"),
            ("tab", "Tab"),
            ("enter", "Enter"),
            ("tty", "TTY"),
        ):
            self.add_item(
                XorgControlButton(action=action, label=label, row=3)
            )


class XorgSession:
    """Headless X11 desktop rendered as a Discord image attachment."""

    def __init__(
        self,
        bot: AlpineBot,
        record: UserContainer,
        thread: discord.Thread,
        cog: object,
    ) -> None:
        self.bot = bot
        self.record = record
        self.thread = thread
        self.cog = cog
        self.state = XorgControlState()
        self.message: discord.Message | None = None
        self.render_lock = asyncio.Lock()
        self.started = False

    def view(self) -> XorgControlView:
        return XorgControlView(self.cog, self.state)

    async def start(self) -> None:
        await self.bot.docker.ensure_xorg(self.record.container_id)
        self.started = True
        await self.refresh()

    def build_embed(self) -> discord.Embed:
        modifiers = [
            name
            for enabled, name in ((self.state.ctrl, "Ctrl"), (self.state.alt, "Alt"))
            if enabled
        ]
        modifier_text = "+".join(modifiers) if modifiers else "none"
        held = [
            name
            for enabled, name in (
                (self.state.left_down, "left"),
                (self.state.right_down, "right"),
            )
            if enabled
        ]
        held_text = ", ".join(held) if held else "none"
        embed = discord.Embed(
            title="Alpine Linux Xorg",
            description=(
                "Send a message to type into the focused X11 window. "
                "Messages without queued modifiers also press Enter.\n"
                f"**Mouse step:** {self.state.step}px · "
                f"**Queued:** {modifier_text} · **Held buttons:** {held_text}"
            ),
            colour=discord.Colour.blue(),
        )
        embed.set_image(url="attachment://xorg.png")
        embed.set_footer(text="Xorg display • 800×600 • Xvfb :99")
        return embed

    async def refresh(self) -> None:
        async with self.render_lock:
            image = await self.bot.docker.xorg_screenshot(self.record.container_id)
            file = discord.File(io.BytesIO(image), filename="xorg.png")
            if self.message is None and self.record.xorg_message_id:
                try:
                    self.message = await self.thread.fetch_message(
                        self.record.xorg_message_id
                    )
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    self.message = None

            if self.message is not None:
                try:
                    self.message = await self.message.edit(
                        content=None,
                        embed=self.build_embed(),
                        attachments=[file],
                        view=self.view(),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.NotFound:
                    self.message = None
                    file = discord.File(io.BytesIO(image), filename="xorg.png")
            if self.message is None:
                self.message = await self.thread.send(
                    embed=self.build_embed(),
                    file=file,
                    view=self.view(),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            self.record.xorg_message_id = self.message.id
            self.record.display_mode = "xorg"
            await self.bot.db.set_display(
                self.record.user_id,
                mode="xorg",
                terminal_message_id=None,
                xorg_message_id=self.message.id,
            )

    async def move(self, direction: str) -> None:
        offsets = {
            "up": (0, -self.state.step),
            "down": (0, self.state.step),
            "left": (-self.state.step, 0),
            "right": (self.state.step, 0),
        }
        if direction not in offsets:
            raise ValueError(f"Unknown mouse direction: {direction}")
        x, y = offsets[direction]
        await self.bot.docker.xorg_action(
            self.record.container_id,
            ["mousemove_relative", "--sync", "--", str(x), str(y)],
        )

    async def toggle_mouse_button(self, button: int) -> None:
        if button == 1:
            down = self.state.left_down
            self.state.left_down = not down
        elif button == 3:
            down = self.state.right_down
            self.state.right_down = not down
        else:
            raise ValueError(f"Unsupported mouse button: {button}")
        await self.bot.docker.xorg_action(
            self.record.container_id,
            ["mouseup" if down else "mousedown", str(button)],
        )

    async def send_special(self, key: str) -> None:
        names = []
        if self.state.ctrl:
            names.append("ctrl")
        if self.state.alt:
            names.append("alt")
        names.append(key)
        await self.bot.docker.xorg_action(
            self.record.container_id,
            ["key", "--clearmodifiers", "+".join(names)],
        )
        self.state.ctrl = False
        self.state.alt = False

    async def send_text(self, text: str) -> None:
        if self.state.ctrl or self.state.alt:
            first = text[0]
            if not first.isascii() or not first.isalnum():
                raise ValueError(
                    "Ctrl/Alt must be followed by an ASCII letter or number"
                )
            names = []
            if self.state.ctrl:
                names.append("ctrl")
            if self.state.alt:
                names.append("alt")
            names.append(first.lower())
            await self.bot.docker.xorg_action(
                self.record.container_id,
                ["key", "--clearmodifiers", "+".join(names)],
            )
            if text[1:]:
                await self.bot.docker.xorg_action(
                    self.record.container_id,
                    ["type", "--clearmodifiers", "--delay", "1", "--", text[1:]],
                )
            self.state.ctrl = False
            self.state.alt = False
            return

        await self.bot.docker.xorg_action(
            self.record.container_id,
            ["type", "--clearmodifiers", "--delay", "1", "--", text],
        )
        await self.bot.docker.xorg_action(
            self.record.container_id,
            ["key", "Return"],
        )

    async def release_mouse_buttons(self) -> None:
        for down, button in (
            (self.state.left_down, 1),
            (self.state.right_down, 3),
        ):
            if down:
                try:
                    await self.bot.docker.xorg_action(
                        self.record.container_id, ["mouseup", str(button)]
                    )
                except Exception:
                    pass
        self.state.left_down = False
        self.state.right_down = False

    async def hide(self) -> None:
        await self.release_mouse_buttons()
        if self.message is not None:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        self.message = None
        self.record.xorg_message_id = None
