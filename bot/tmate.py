from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# tmate compares SHA256 fingerprints as "SHA256:" plus unpadded base64.
_FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9._:\[\]-]+$")

_TMATE_SOCKET = "/tmp/alpine-tmate.sock"
_TMATE_CONFIG = "/tmp/alpine-tmate.conf"


@dataclass(frozen=True)
class TmateSettings:
    """Where the tmate client connects and how to authenticate the relay."""

    server_host: str = ""
    server_port: int = 22
    keys_path: Path = Path("data/tmate")
    rsa_fingerprint: str = ""
    ed25519_fingerprint: str = ""

    def __post_init__(self) -> None:
        if not 1 <= self.server_port <= 65535:
            raise RuntimeError(
                f"TMATE_SERVER_PORT must be between 1 and 65535, got {self.server_port}"
            )
        if self.server_host and not _HOST_RE.fullmatch(self.server_host):
            raise RuntimeError(
                "TMATE_SERVER_HOST may contain only letters, digits, '.', '_', "
                "':', '[', ']', and '-'"
            )
        for name, value in (
            ("TMATE_SERVER_RSA_FINGERPRINT", self.rsa_fingerprint),
            ("TMATE_SERVER_ED25519_FINGERPRINT", self.ed25519_fingerprint),
        ):
            if value and not _FINGERPRINT_RE.fullmatch(value):
                raise RuntimeError(
                    f"{name} must look like SHA256: followed by 43 base64 characters"
                )

    @property
    def configured(self) -> bool:
        """True when a host and at least one verifiable host key exist."""
        if not self.server_host:
            return False
        try:
            rsa, ed25519 = self.fingerprints()
        except RuntimeError:
            return False
        return bool(rsa or ed25519)

    def fingerprints(self) -> tuple[str, str]:
        rsa = self.rsa_fingerprint or _fingerprint_from(
            self.keys_path / "ssh_host_rsa_key.pub"
        )
        ed25519 = self.ed25519_fingerprint or _fingerprint_from(
            self.keys_path / "ssh_host_ed25519_key.pub"
        )
        if not rsa and not ed25519:
            raise RuntimeError(
                "tmate is not configured: set TMATE_SERVER_HOST and either "
                "TMATE_SERVER_RSA_FINGERPRINT/TMATE_SERVER_ED25519_FINGERPRINT "
                "or point TMATE_KEYS_PATH at the relay's host keys."
            )
        return rsa, ed25519


def ssh_fingerprint(public_key: str) -> str:
    """Return the OpenSSH SHA256 fingerprint of an authorized_keys line."""
    parts = public_key.strip().split()
    if len(parts) < 2:
        raise ValueError("not an SSH public key")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("invalid base64 in SSH public key") from error
    digest = hashlib.sha256(blob).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


def _fingerprint_from(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as error:
        raise RuntimeError(f"Could not read tmate host key {path}: {error}") from error
    try:
        return ssh_fingerprint(text)
    except ValueError as error:
        raise RuntimeError(f"Invalid tmate host key {path}: {error}") from error


def build_config(settings: TmateSettings) -> str:
    rsa, ed25519 = settings.fingerprints()
    lines = [
        f"set -g tmate-server-host {settings.server_host}",
        f"set -g tmate-server-port {settings.server_port}",
    ]
    if rsa:
        lines.append(f"set -g tmate-server-rsa-fingerprint {rsa}")
    if ed25519:
        lines.append(f"set -g tmate-server-ed25519-fingerprint {ed25519}")
    return "\n".join(lines) + "\n"


def build_command(settings: TmateSettings, *, stop: bool = False) -> list[str]:
    """Build the container command that starts or stops the shared session."""
    if stop:
        script = f"""set -eu
if [ -S {_TMATE_SOCKET} ]; then
    tmate -S {_TMATE_SOCKET} kill-server >/dev/null 2>&1 || true
fi
rm -f {_TMATE_SOCKET} {_TMATE_CONFIG}
echo "tmate session stopped"
"""
        return ["/bin/sh", "-c", script]

    config = base64.b64encode(build_config(settings).encode("utf-8")).decode("ascii")
    script = f"""set -eu
cd /root
umask 077
printf '%s' '{config}' | base64 -d > {_TMATE_CONFIG}
if [ -S {_TMATE_SOCKET} ] && tmate -S {_TMATE_SOCKET} has-session -t alpine >/dev/null 2>&1; then
    tmate -S {_TMATE_SOCKET} display -p '#{{tmate_ssh}}'
    exit 0
fi
if [ -S {_TMATE_SOCKET} ]; then
    tmate -S {_TMATE_SOCKET} kill-server >/dev/null 2>&1 || true
    rm -f {_TMATE_SOCKET}
fi
tmate -f {_TMATE_CONFIG} -S {_TMATE_SOCKET} new-session -d -s alpine
if ! timeout 25 tmate -S {_TMATE_SOCKET} wait tmate-ready; then
    tmate -S {_TMATE_SOCKET} kill-server >/dev/null 2>&1 || true
    rm -f {_TMATE_SOCKET}
    echo "tmate could not reach the relay before the timeout" >&2
    exit 1
fi
tmate -S {_TMATE_SOCKET} display -p '#{{tmate_ssh}}'
"""
    return ["/bin/sh", "-c", script]
