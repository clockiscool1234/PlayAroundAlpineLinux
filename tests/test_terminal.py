from __future__ import annotations

import io
import unittest

from bot.db import UserContainer
from bot.docker_manager import TerminalConnection
from bot.terminal import TerminalSession


class FakeSocket:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data) -> int:
        chunk = bytes(data)
        self.data.extend(chunk)
        return len(chunk)


class FakeRawSocket:
    def __init__(self) -> None:
        self.data = bytearray()

    def sendall(self, data) -> None:
        self.data.extend(data)


class ReadOnlySocketWrapper:
    def __init__(self) -> None:
        self._sock = FakeRawSocket()

    def write(self, data) -> int:
        raise io.UnsupportedOperation("File or stream is not writable")


class TerminalInputTests(unittest.IsolatedAsyncioTestCase):
    def session(self) -> tuple[TerminalSession, FakeSocket]:
        record = UserContainer(
            user_id=1,
            guild_id=2,
            thread_id=3,
            container_id="container",
            container_name="discord-alpine-1",
            created_at=0,
            status="running",
        )
        session = TerminalSession(None, record, None)  # type: ignore[arg-type]
        socket = FakeSocket()
        session.connection = TerminalConnection(exec_id="exec", socket=socket)
        session.alive = True
        return session, socket

    async def test_plain_message_is_a_command_with_enter(self) -> None:
        session, socket = self.session()
        await session.send_message_input("echo hello")
        self.assertEqual(socket.data, b"echo hello\n")

    async def test_ctrl_arrow_uses_modified_escape_sequence(self) -> None:
        session, socket = self.session()
        await session.send_key_chord("up", ctrl=True, alt=False)
        self.assertEqual(socket.data, b"\x1b[1;5A")

    async def test_alt_arrow_uses_modified_escape_sequence(self) -> None:
        session, socket = self.session()
        await session.send_key_chord("up", ctrl=False, alt=True)
        self.assertEqual(socket.data, b"\x1b[1;3A")

    async def test_terminal_embed_is_padded_to_configured_size(self) -> None:
        session, _ = self.session()
        description = session.build_embed().description
        self.assertIsNotNone(description)
        terminal = description.removeprefix("```\n").removesuffix("\n```")  # type: ignore[union-attr]
        lines = terminal.split("\n")
        self.assertEqual(len(lines), 22)
        self.assertTrue(all(len(line) == 80 for line in lines))

    async def test_docker_read_only_wrapper_uses_underlying_socket(self) -> None:
        session, _ = self.session()
        wrapper = ReadOnlySocketWrapper()
        session.connection = TerminalConnection(exec_id="exec", socket=wrapper)

        await session.send_message_input("pwd")

        self.assertEqual(wrapper._sock.data, b"pwd\n")


if __name__ == "__main__":
    unittest.main()
