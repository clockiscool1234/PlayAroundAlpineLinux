from __future__ import annotations

import unittest

from docker.errors import NotFound

from bot.docker_manager import DockerManager


class FakeContainers:
    def __init__(self) -> None:
        self.run_kwargs = None
        self.container = None

    def get(self, identifier):
        if self.container is None:
            raise NotFound("not found")
        return self.container

    def run(self, **kwargs):
        self.run_kwargs = kwargs
        return FakeContainer()


class FakeContainer:
    id = "container-id"
    name = "discord-alpine-123"
    status = "running"

    def __init__(self) -> None:
        self.restart_timeout = None

    def reload(self):
        return None

    def restart(self, *, timeout):
        self.restart_timeout = timeout


class FakeAPI:
    def __init__(self) -> None:
        self.resize = None
        self.create_kwargs = None
        self.start_kwargs = None
        self.create_args = None

    def exec_create(self, *args, **kwargs):
        self.create_args = args
        self.create_kwargs = kwargs
        return {"Id": "exec-id"}

    def exec_start(self, *args, **kwargs):
        self.start_kwargs = kwargs
        return FakeSocket()

    def exec_resize(self, exec_id, *, height, width):
        self.resize = (exec_id, height, width)


class FakeSocket:
    def __init__(self) -> None:
        self.timeout = "unchanged"

    def settimeout(self, value) -> None:
        self.timeout = value


class FakeClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()
        self.api = FakeAPI()


class DockerManagerTests(unittest.IsolatedAsyncioTestCase):
    def manager(self) -> tuple[DockerManager, FakeClient]:
        manager = DockerManager(
            image="alpine:3.20",
            memory="512m",
            disk="1g",
            cpus=1.0,
            pids_limit=128,
            network_mode="none",
            http_proxy="http://172.30.0.2:3128",
        )
        client = FakeClient()
        manager._client = client
        return manager, client

    async def test_container_has_requested_limits(self) -> None:
        manager, client = self.manager()
        await manager.create_container(guild_id=5, user_id=123)

        kwargs = client.containers.run_kwargs
        self.assertEqual(kwargs["mem_limit"], "512m")
        self.assertEqual(kwargs["memswap_limit"], "512m")
        self.assertEqual(kwargs["storage_opt"], {"size": "1g"})
        self.assertEqual(kwargs["nano_cpus"], 1_000_000_000)
        self.assertEqual(kwargs["pids_limit"], 128)
        self.assertEqual(kwargs["network_mode"], "none")
        self.assertEqual(
            kwargs["environment"]["HTTPS_PROXY"],
            "http://172.30.0.2:3128",
        )
        self.assertEqual(kwargs["environment"]["DISPLAY"], ":99")
        self.assertEqual(kwargs["cap_drop"], ["ALL"])
        self.assertEqual(kwargs["security_opt"], ["no-new-privileges:true"])
        self.assertEqual(kwargs["restart_policy"], {"Name": "unless-stopped"})

    async def test_terminal_uses_requested_pty_size(self) -> None:
        manager, client = self.manager()
        client.containers.container = FakeContainer()

        connection = await manager.open_terminal(
            "container-id", columns=80, rows=22
        )

        self.assertEqual(connection.exec_id, "exec-id")
        self.assertIsInstance(connection.socket, FakeSocket)
        self.assertIsNone(connection.socket.timeout)
        self.assertTrue(client.api.create_kwargs["stdin"])
        self.assertTrue(client.api.create_kwargs["tty"])
        self.assertEqual(client.api.create_kwargs["environment"]["DISPLAY"], ":99")
        self.assertEqual(client.api.start_kwargs, {"tty": True, "socket": True})
        self.assertEqual(client.api.resize, ("exec-id", 22, 80))
        command = client.api.create_args[1]
        self.assertIn("cat /etc/motd", command[2])
        self.assertIn("exec /bin/sh -l", command[2])

    async def test_restart_container(self) -> None:
        manager, client = self.manager()
        container = FakeContainer()
        client.containers.container = container

        self.assertTrue(await manager.restart_container("container-id", timeout=7))
        self.assertEqual(container.restart_timeout, 7)


if __name__ == "__main__":
    unittest.main()
