from __future__ import annotations

import unittest
from unittest import mock

from docker.errors import NotFound

from bot.docker_manager import DockerManager


class FakeProbe:
    def __init__(
        self,
        status: int = 1,
        logs: bytes = b"dd: /disk-probe.bin: No space left on device",
    ) -> None:
        self.status = status
        self.logs_data = logs
        self.removed = False
        self.wait_timeout = None

    def wait(self, timeout=None):
        self.wait_timeout = timeout
        return {"StatusCode": self.status}

    def logs(self):
        return self.logs_data

    def remove(self, force=False):
        self.removed = True


class FakeContainers:
    def __init__(self) -> None:
        self.run_kwargs = None
        self.container = None
        self.probe = FakeProbe()
        self.probe_kwargs = None
        self.container_result = FakeContainer()
        self.run_count = 0

    def get(self, identifier):
        if self.container is None:
            raise NotFound("not found")
        return self.container

    def run(self, **kwargs):
        self.run_count += 1
        if kwargs.get("storage_opt", {}).get("size") == "8m":
            self.probe_kwargs = kwargs
            return self.probe
        self.run_kwargs = kwargs
        return self.container_result


class FakeContainer:
    id = "container-id"
    name = "discord-alpine-123"
    status = "running"

    def __init__(
        self,
        storage_size: str = "1g",
        memory: int = 512 * 1024 * 1024,
        nano_cpus: int = 1_000_000_000,
        pids_limit: int = 128,
    ) -> None:
        self.restart_timeout = None
        self.attrs = {
            "HostConfig": {
                "StorageOpt": {"size": storage_size},
                "Memory": memory,
                "MemorySwap": memory,
                "NanoCpus": nano_cpus,
                "PidsLimit": pids_limit,
            }
        }

    def reload(self):
        return None

    def restart(self, *, timeout):
        self.restart_timeout = timeout

    def remove(self, force=False):
        return None


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
        self.info_data = {
            "Driver": "overlay2",
            "DriverStatus": [["Backing Filesystem", "xfs"]],
        }

    def info(self):
        return self.info_data


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
        self.assertEqual(
            client.containers.probe_kwargs["storage_opt"],
            {"size": "8m"},
        )
        self.assertTrue(client.containers.probe.removed)

    async def test_lxcfs_mounts_are_added_when_configured(self) -> None:
        manager, client = self.manager()
        manager.lxcfs_path = "/var/lib/lxcfs"
        await manager.create_container(guild_id=5, user_id=123)

        volumes = client.containers.run_kwargs["volumes"]
        self.assertEqual(
            volumes["/var/lib/lxcfs/proc/meminfo"]["bind"], "/proc/meminfo"
        )
        self.assertEqual(
            volumes["/var/lib/lxcfs/proc/cpuinfo"]["bind"], "/proc/cpuinfo"
        )
        self.assertEqual(volumes["/var/lib/lxcfs/proc/meminfo"]["mode"], "ro")

    async def test_disk_quota_rejects_non_xfs_overlay(self) -> None:
        manager, client = self.manager()
        client.info_data = {
            "Driver": "overlay2",
            "DriverStatus": [["Backing Filesystem", "extfs"]],
        }

        with self.assertRaisesRegex(RuntimeError, "backed by extfs"):
            await manager.ensure_disk_quota()

        self.assertEqual(client.containers.run_count, 0)

    async def test_disk_quota_rejects_containerd_image_store(self) -> None:
        manager, client = self.manager()
        client.info_data = {
            "Driver": "overlayfs",
            "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]],
        }

        with self.assertRaisesRegex(RuntimeError, "containerd image store"):
            await manager.ensure_disk_quota()

        self.assertEqual(client.containers.run_count, 0)

    async def test_disk_quota_probe_rejects_unenforced_quota(self) -> None:
        manager, client = self.manager()
        client.containers.probe.status = 0
        client.containers.probe.logs_data = b""

        with self.assertRaisesRegex(RuntimeError, "did not enforce"):
            await manager.ensure_disk_quota()

        self.assertTrue(client.containers.probe.removed)

    async def test_disk_quota_probe_rejects_unrelated_failure(self) -> None:
        manager, client = self.manager()
        client.containers.probe.status = 1
        client.containers.probe.logs_data = b"dd: can't open '/disk-probe.bin'"

        with self.assertRaisesRegex(RuntimeError, "could not verify"):
            await manager.ensure_disk_quota()

    async def test_create_refuses_after_quota_verification_failure(self) -> None:
        manager, client = self.manager()
        manager._disk_quota_checked = True
        manager._disk_quota_error = "quota is not supported"

        with self.assertRaisesRegex(RuntimeError, "quota is not supported"):
            await manager.create_container(guild_id=5, user_id=123)

        self.assertEqual(client.containers.run_count, 0)

    async def test_create_rejects_container_without_requested_quota(self) -> None:
        manager, client = self.manager()
        client.containers.container_result = FakeContainer(storage_size="")

        with self.assertRaisesRegex(RuntimeError, "without the requested"):
            await manager.create_container(guild_id=5, user_id=123)

    async def test_create_rejects_container_without_requested_limits(self) -> None:
        manager, client = self.manager()
        container = FakeContainer(memory=0)
        client.containers.container_result = container
        removed = []
        container.remove = lambda force=False: removed.append(force)  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "memory/CPU/PID limits"):
            await manager.create_container(guild_id=5, user_id=123)

        self.assertEqual(removed, [True])

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

    async def test_container_limits_reads_host_config(self) -> None:
        manager, client = self.manager()
        client.containers.container = FakeContainer()

        limits = await manager.container_limits("container-id")

        self.assertEqual(limits["memory"], 512 * 1024 * 1024)
        self.assertEqual(limits["nano_cpus"], 1_000_000_000)
        self.assertEqual(limits["pids_limit"], 128)
        self.assertEqual(limits["disk"], "1g")

    async def test_tmate_requires_networking(self) -> None:
        manager, _ = self.manager()
        manager.tmate = manager.tmate.__class__(
            server_host="tmate",
            ed25519_fingerprint="SHA256:" + "a" * 43,
        )

        with self.assertRaisesRegex(RuntimeError, "needs container networking"):
            await manager.tmate_session("container-id")

    async def test_tmate_is_disabled_without_relay_config(self) -> None:
        manager, _ = self.manager()
        manager.network_mode = "alpine-filtered"

        with self.assertRaisesRegex(RuntimeError, "tmate is not configured"):
            await manager.tmate_session("container-id")

    async def test_tmate_returns_ssh_command(self) -> None:
        manager, _ = self.manager()
        manager.network_mode = "alpine-filtered"
        manager.tmate = manager.tmate.__class__(
            server_host="tmate",
            server_port=2222,
            ed25519_fingerprint="SHA256:" + "a" * 43,
        )
        manager.run_command = mock.AsyncMock(return_value=b"ssh abc@tmate\n")

        output = await manager.tmate_session("container-id")

        self.assertEqual(output, "ssh abc@tmate")

    async def test_restart_container(self) -> None:
        manager, client = self.manager()
        container = FakeContainer()
        client.containers.container = container

        self.assertTrue(await manager.restart_container("container-id", timeout=7))
        self.assertEqual(container.restart_timeout, 7)


if __name__ == "__main__":
    unittest.main()
