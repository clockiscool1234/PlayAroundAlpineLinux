from __future__ import annotations

import unittest
from types import SimpleNamespace

from bot.db import UserContainer
from bot.xorg import XorgSession


class FakeDocker:
    def __init__(self) -> None:
        self.actions: list[list[str]] = []

    async def xorg_action(self, container_id: str, arguments: list[str]) -> None:
        self.actions.append(arguments)


class XorgInputTests(unittest.IsolatedAsyncioTestCase):
    def session(self) -> tuple[XorgSession, FakeDocker]:
        docker = FakeDocker()
        bot = SimpleNamespace(docker=docker)
        record = UserContainer(
            user_id=1,
            guild_id=2,
            thread_id=3,
            container_id="container",
            container_name="discord-alpine-1",
            created_at=0,
            status="running",
        )
        session = XorgSession(bot, record, None, None)  # type: ignore[arg-type]
        return session, docker

    async def test_plain_message_types_text_and_presses_enter(self) -> None:
        session, docker = self.session()

        await session.send_text("echo hello")

        self.assertEqual(
            docker.actions,
            [
                ["type", "--clearmodifiers", "--delay", "1", "--", "echo hello"],
                ["key", "Return"],
            ],
        )

    async def test_modifiers_apply_to_first_character_only(self) -> None:
        session, docker = self.session()
        session.state.ctrl = True
        session.state.alt = True

        await session.send_text("ax")

        self.assertEqual(
            docker.actions,
            [
                ["key", "--clearmodifiers", "ctrl+alt+a"],
                ["type", "--clearmodifiers", "--delay", "1", "--", "x"],
            ],
        )
        self.assertFalse(session.state.ctrl)
        self.assertFalse(session.state.alt)

    async def test_enter_sends_return_key(self) -> None:
        session, docker = self.session()

        await session.send_special("Return")

        self.assertEqual(
            docker.actions,
            [["key", "--clearmodifiers", "Return"]],
        )

    async def test_mouse_step_controls_relative_movement(self) -> None:
        session, docker = self.session()
        session.state.step = 100

        await session.move("left")

        self.assertEqual(
            docker.actions,
            [["mousemove_relative", "--sync", "--", "-100", "0"]],
        )

    async def test_mouse_button_toggles_down_and_up(self) -> None:
        session, docker = self.session()

        await session.toggle_mouse_button(1)
        await session.toggle_mouse_button(1)

        self.assertEqual(
            docker.actions,
            [["mousedown", "1"], ["mouseup", "1"]],
        )
        self.assertFalse(session.state.left_down)


if __name__ == "__main__":
    unittest.main()
