from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

import docker
from docker.errors import ImageNotFound, NotFound
from docker.models.containers import Container

log = logging.getLogger(__name__)


@dataclass
class TerminalConnection:
    exec_id: str
    socket: Any


class DockerManager:
    def __init__(
        self,
        *,
        image: str,
        memory: str,
        disk: str,
        cpus: float,
        pids_limit: int,
        network_mode: str,
        http_proxy: str,
    ) -> None:
        self.image = image
        self.memory = memory
        self.disk = disk
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.network_mode = network_mode
        self.http_proxy = http_proxy
        self._client: Optional[docker.DockerClient] = None

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def _container_name(self, user_id: int) -> str:
        return f"discord-alpine-{user_id}"

    async def ensure_image(self) -> None:
        def _pull() -> None:
            try:
                self.client.images.get(self.image)
            except ImageNotFound:
                log.info("Pulling image %s", self.image)
                self.client.images.pull(self.image)

        await asyncio.to_thread(_pull)

    async def ensure_network(self) -> None:
        if self.network_mode in {"none", "host", "bridge", "default"}:
            return

        def _inspect() -> None:
            try:
                self.client.networks.get(self.network_mode)
            except NotFound as error:
                raise RuntimeError(
                    f"Docker network {self.network_mode!r} does not exist. "
                    "Start the network filter with `docker compose up -d network-filter`."
                ) from error

        await asyncio.to_thread(_inspect)

    async def create_container(self, guild_id: int, user_id: int) -> Container:
        name = self._container_name(user_id)

        def _create() -> Container:
            # Remove stale container with same name if it exists but is not tracked.
            try:
                existing = self.client.containers.get(name)
                try:
                    existing.remove(force=True)
                except Exception:
                    log.warning("Failed to remove stale container %s", name)
            except NotFound:
                pass

            kwargs = dict(
                image=self.image,
                name=name,
                command=["sleep", "infinity"],
                detach=True,
                stdin_open=False,
                tty=False,
                mem_limit=self.memory,
                memswap_limit=self.memory,  # no swap beyond memory limit
                nano_cpus=int(self.cpus * 1_000_000_000),
                pids_limit=self.pids_limit,
                network_mode=self.network_mode,
                security_opt=["no-new-privileges:true"],
                cap_drop=["ALL"],
                init=True,
                labels={
                    "managed_by": "alpine-discord-bot",
                    "guild_id": str(guild_id),
                    "user_id": str(user_id),
                },
                environment=self._container_environment(),
                restart_policy={"Name": "unless-stopped"},
            )

            # Fail closed: an unsupported quota must not result in an unlimited
            # writable layer. overlay2 typically requires XFS with project quotas.
            if not self.disk:
                raise RuntimeError("CONTAINER_DISK must be set to enforce a disk quota")
            return self.client.containers.run(
                **kwargs,
                storage_opt={"size": self.disk},
            )

        return await asyncio.to_thread(_create)

    def _container_environment(self) -> dict[str, str]:
        environment = {"TERM": "xterm", "DISPLAY": ":99"}
        if self.http_proxy:
            environment.update(
                {
                    "HTTP_PROXY": self.http_proxy,
                    "HTTPS_PROXY": self.http_proxy,
                    "http_proxy": self.http_proxy,
                    "https_proxy": self.http_proxy,
                    "NO_PROXY": "localhost,127.0.0.1,::1",
                    "no_proxy": "localhost,127.0.0.1,::1",
                }
            )
        return environment

    async def get_container(self, container_id: str) -> Optional[Container]:
        def _get() -> Optional[Container]:
            try:
                return self.client.containers.get(container_id)
            except NotFound:
                return None

        return await asyncio.to_thread(_get)

    async def start_container(self, container_id: str) -> bool:
        container = await self.get_container(container_id)
        if container is None:
            return False

        def _start() -> None:
            container.reload()
            if container.status != "running":
                container.start()

        await asyncio.to_thread(_start)
        return True

    async def stop_container(self, container_id: str, timeout: int = 5) -> bool:
        container = await self.get_container(container_id)
        if container is None:
            return False

        def _stop() -> None:
            container.stop(timeout=timeout)

        await asyncio.to_thread(_stop)
        return True

    async def restart_container(self, container_id: str, timeout: int = 5) -> bool:
        container = await self.get_container(container_id)
        if container is None:
            return False

        await asyncio.to_thread(container.restart, timeout=timeout)
        return True

    async def remove_container(self, container_id: str) -> bool:
        container = await self.get_container(container_id)
        if container is None:
            return False

        def _remove() -> None:
            try:
                container.remove(force=True)
            except NotFound:
                pass

        await asyncio.to_thread(_remove)
        return True

    async def open_terminal(
        self,
        container_id: str,
        *,
        columns: int = 80,
        rows: int = 22,
    ) -> TerminalConnection:
        container = await self.get_container(container_id)
        if container is None:
            raise RuntimeError("Container not found")
        await asyncio.to_thread(container.reload)
        if container.status != "running":
            raise RuntimeError(f"Container is not running (status: {container.status})")

        def _open() -> TerminalConnection:
            created = self.client.api.exec_create(
                container.id,
                [
                    "/bin/sh",
                    "-c",
                    "if [ -r /etc/motd ]; then cat /etc/motd; fi; "
                    "exec /bin/sh -l",
                ],
                stdin=True,
                stdout=True,
                stderr=True,
                tty=True,
                workdir="/root",
                environment={
                    "TERM": "xterm-256color",
                    "HOME": "/root",
                    "DISPLAY": ":99",
                    "COLUMNS": str(columns),
                    "LINES": str(rows),
                },
            )
            exec_id = created["Id"]
            socket = self.client.api.exec_start(exec_id, tty=True, socket=True)
            try:
                # Docker SDK inherits the HTTP client's 60-second timeout on
                # this hijacked connection. A TTY must remain blocking; its
                # lifecycle is managed by TerminalSession's idle timer.
                transport = getattr(socket, "_sock", socket)
                settimeout = getattr(transport, "settimeout", None)
                if settimeout is not None:
                    settimeout(None)
                self.client.api.exec_resize(exec_id, height=rows, width=columns)
            except Exception:
                socket.close()
                raise
            return TerminalConnection(exec_id=exec_id, socket=socket)

        return await asyncio.to_thread(_open)

    async def resize_terminal(
        self, exec_id: str, *, columns: int, rows: int
    ) -> None:
        await asyncio.to_thread(
            self.client.api.exec_resize,
            exec_id,
            height=rows,
            width=columns,
        )

    async def container_status(self, container_id: str) -> Optional[str]:
        container = await self.get_container(container_id)
        if container is None:
            return None

        def _status() -> str:
            container.reload()
            return container.status

        return await asyncio.to_thread(_status)

    async def run_command(
        self,
        container_id: str,
        command: list[str],
        *,
        environment: dict[str, str] | None = None,
        max_output: int = 8 * 1024 * 1024,
    ) -> bytes:
        container = await self.get_container(container_id)
        if container is None:
            raise RuntimeError("Container not found")
        await asyncio.to_thread(container.reload)
        if container.status != "running":
            raise RuntimeError(f"Container is not running (status: {container.status})")

        def _run() -> bytes:
            result = container.exec_run(
                command,
                environment=environment,
                demux=False,
            )
            output = result.output or b""
            if isinstance(output, str):
                output = output.encode("utf-8", errors="replace")
            if len(output) > max_output:
                raise RuntimeError(
                    f"Container command output exceeded {max_output} bytes"
                )
            if result.exit_code != 0:
                detail = output.decode("utf-8", errors="replace").strip()
                raise RuntimeError(
                    f"Container command exited with {result.exit_code}"
                    + (f": {detail[:1000]}" if detail else "")
                )
            return output

        return await asyncio.to_thread(_run)

    async def ensure_xorg(self, container_id: str) -> None:
        script = r"""
set -eu
for command in Xvfb xdotool import convert xterm openbox; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing $command. Recreate this container with the Xorg-enabled user image." >&2
        exit 127
    fi
done

if [ ! -s /tmp/alpine-xvfb.pid ] || ! kill -0 "$(cat /tmp/alpine-xvfb.pid)" 2>/dev/null; then
    rm -f /tmp/.X99-lock
    nohup Xvfb :99 -screen 0 800x600x24 -nolisten tcp -ac \
        </dev/null >/tmp/alpine-xvfb.log 2>&1 &
    echo $! >/tmp/alpine-xvfb.pid
fi

ready=0
for delay in 1 1 1 1 1; do
    if DISPLAY=:99 xdotool getmouselocation >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep "$delay"
done
if [ "$ready" -ne 1 ]; then
    cat /tmp/alpine-xvfb.log >&2 || true
    exit 1
fi

# Recover safely if the bot previously stopped while a toggleable button was held.
DISPLAY=:99 xdotool mouseup 1 >/dev/null 2>&1 || true
DISPLAY=:99 xdotool mouseup 3 >/dev/null 2>&1 || true

if [ ! -s /tmp/alpine-openbox.pid ] || ! kill -0 "$(cat /tmp/alpine-openbox.pid)" 2>/dev/null; then
    nohup env DISPLAY=:99 openbox </dev/null >/tmp/alpine-openbox.log 2>&1 &
    echo $! >/tmp/alpine-openbox.pid
fi

if ! DISPLAY=:99 xdotool search --class XTerm >/dev/null 2>&1; then
    nohup env DISPLAY=:99 xterm -geometry 96x32+8+8 \
        </dev/null >/tmp/alpine-xterm.log 2>&1 &
fi
sleep 1
"""
        await self.run_command(
            container_id,
            ["/bin/sh", "-c", script],
            max_output=64 * 1024,
        )

    async def xorg_screenshot(self, container_id: str) -> bytes:
        # X11 root-window captures omit the cursor. Draw a small red/white
        # target at xdotool's reported location so Discord users can see it.
        script = r"""
set -eu
eval "$(DISPLAY=:99 xdotool getmouselocation --shell)"
DISPLAY=:99 import -window root miff:- | \
    convert miff:- -stroke white -strokewidth 5 -fill none \
        -draw "circle $X,$Y $((X + 7)),$Y" \
        -stroke red -strokewidth 2 \
        -draw "circle $X,$Y $((X + 7)),$Y" png:-
"""
        image = await self.run_command(
            container_id,
            ["/bin/sh", "-c", script],
            environment={"DISPLAY": ":99"},
            max_output=7 * 1024 * 1024,
        )
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Xorg screenshot command did not return a PNG image")
        return image

    async def xorg_action(self, container_id: str, arguments: list[str]) -> None:
        await self.run_command(
            container_id,
            ["xdotool", *arguments],
            environment={"DISPLAY": ":99"},
            max_output=64 * 1024,
        )
