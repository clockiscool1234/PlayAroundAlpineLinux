# Alpine Container Discord Bot

A `discord.py` bot that gives each Discord user one personal Alpine Linux Docker container. A user runs `/givcontainerpls`; the bot creates a private control thread, adds that user, and starts a resource-limited container.

This is an initial implementation designed to be extended as requirements become clearer.

## Current behavior

- One container per Discord user **globally**, even if the bot is in several servers.
- Private control thread containing the bot and requesting user.
- A persistent `80×22` pseudo-terminal. Owner messages are deleted, forwarded to the TTY, and rendered into one edited rich embed.
- ANSI cursor movement and full-screen terminal programs are interpreted with `pyte` rather than posted as raw escape codes.
- The terminal code block is padded to exactly the configured rows and columns.
- Stateful message-component buttons for arrows, Ctrl, Alt, Esc, Tab, and sending key combinations.
- Owner-only slash controls, usable only in the linked thread:
  - `/settings [columns] [rows]`
  - `/printtty`
  - `/status`
  - `/start`
  - `/stop`
  - `/destroycontainer`
- SQLite persistence across bot restarts.
- Default Alpine image: `alpine:3.20`.
- 512 MiB memory limit with no additional swap.
- 1 GiB writable-layer disk quota; creation fails if Docker cannot enforce it.
- One-CPU execution ceiling.
- 128-process limit.
- Filtered HTTP/HTTPS access through an internal-only Docker network and Squid policy proxy.
- All Linux capabilities dropped, `no-new-privileges` enabled, and Docker's default seccomp profile retained.
- Terminal dimensions are bounded so the visible screen always fits in one Discord message.

### Terminal controls

Send a normal message in the private control thread to type that text followed by Enter. The bot deletes the input message and updates its terminal message.

- Click Ctrl and/or Alt to toggle modifiers.
- Select one arrow, Esc, or Tab key. Selecting it again deselects it; selecting another action key replaces it.
- Selected buttons turn blue. Press **Send** to transmit the selected key combination and clear the selection.
- `/settings columns:80 rows:22` resizes both the Docker TTY and terminal renderer.
- `/printtty` deletes previous bot-authored TTY displays and reposts the live screen at the bottom of a crowded thread.

Only the container owner can operate its terminal buttons. Discord is not a low-latency transport, but programs such as editors, pagers, and process monitors can retain terminal state and react to input.

The TTY remains attached while the bot process is running. If the bot restarts, the next input opens a fresh shell in the same container; container files remain, but a foreground interactive program from the old TTY should be considered disconnected.

## Security warning

Docker isolation is not equivalent to a dedicated VM and should not be treated as a perfect hostile multi-tenant sandbox. Run this on a dedicated machine/VM, keep Docker and the kernel patched, and do not store secrets on that host. Mounting `/var/run/docker.sock` gives the **bot process** host-level Docker control, so protect the bot token and its host carefully.

User containers do not receive the Docker socket, host directories, extra capabilities, or a direct Internet route.

## Filtered network access

User containers join the `alpine-filtered` Docker network. That network is marked `internal`, so containers cannot route directly to the Internet even if a user unsets the proxy variables or changes application settings. The `network-filter` service is attached to both the internal network and a normal egress network and is the only web gateway.

The bot injects `HTTP_PROXY`, `HTTPS_PROXY`, and lowercase equivalents into each new container. Squid permits HTTP and HTTPS proxy traffic on ports 80 and 443 while denying:

- domains from [`elbkr/bad-websites`](https://github.com/elbkr/bad-websites);
- active phishing domains and IPs from [`Phishing-Database/Phishing.Database`](https://github.com/Phishing-Database/Phishing.Database);
- domains in `network-filter/custom-blocked-domains.txt`, including common public-IP lookup sites;
- literal IP destinations, which could otherwise bypass domain rules;
- non-HTTP protocols and unsafe destination ports.

The feeds are Git submodules pinned to reviewed commits. Initialize them after cloning:

```bash
git submodule update --init --depth 1
```

Update to the latest upstream revisions with:

```bash
./tools/update_blocklists.sh
git diff --submodule
# Review and commit the new submodule revisions, then:
docker compose restart network-filter
```

Edit `network-filter/custom-blocked-domains.txt` to add local policy entries. The filter regenerates its normalized Squid ACL files whenever the service starts.

### Filtering limitations

This is a denylist-based web policy, not a perfect hostile-user firewall. HTTPS hides URL paths, so filtering is by destination hostname/IP rather than full encrypted URL. A permitted site could proxy content from a blocked site, newly registered threats may not yet be listed, and blocklists can contain false positives. DNS and HTTPS can also be abused as tunneling channels by a determined attacker. For a strict environment, replace the denylist with a small explicit allowlist and run the Docker host in a dedicated VM.

Existing containers retain their creation-time network and environment settings. Destroy and recreate them after enabling this feature.

## Prerequisites

- Python 3.11 or newer
- Docker Engine
- A Discord application with a bot token
- A Docker storage driver/filesystem that supports per-container writable-layer `size` quotas

### Disk quota prerequisite

The bot deliberately fails closed if the 1 GiB disk quota cannot be applied. With `overlay2`, Docker commonly requires an XFS backing filesystem mounted with project quotas (`pquota`/`prjquota`). Check your daemon with:

```bash
docker info
```

If `docker run --storage-opt size=1g alpine:3.20 true` reports that `size` is unsupported, configure a quota-capable Docker data root/storage driver before using the bot. The project will not silently launch an unlimited container.

## Discord setup

1. Create an application at <https://discord.com/developers/applications>.
2. Add a bot and copy its token.
3. Under **Bot > Privileged Gateway Intents**, enable **Message Content Intent**. This is required to receive terminal input messages.
4. Under **OAuth2 > URL Generator**, select `bot` and `applications.commands`.
5. Grant the bot these permissions in the target channel:
   - View Channels
   - Send Messages
   - Send Messages in Threads
   - Embed Links
   - Manage Messages
   - Read Message History
   - Create Private Threads
   - Manage Threads
6. Invite the generated URL to your server.

## Run locally

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
git submodule update --init --depth 1
cp .env.example .env
```

Edit `.env` and set `DISCORD_TOKEN`. During development, set `GUILD_ID` to your test server ID so slash-command changes appear immediately.

Start the filtering proxy, then ensure your account can access Docker:

```bash
docker compose up -d network-filter
docker info
python run.py
```

If Docker reports permission denied, configure Docker access for the service account. Membership in the `docker` group effectively grants root-equivalent host access.

## Run with Docker Compose

```bash
git submodule update --init --depth 1
cp .env.example .env
# Edit .env first
docker compose up --build -d
docker compose logs -f bot
```

Compose mounts the host Docker socket so the bot can create sibling containers. User-container limits are applied by the host Docker daemon.

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `DISCORD_TOKEN` | required | Discord bot token |
| `GUILD_ID` | unset | Test guild for immediate command sync; unset means global sync |
| `CONTAINER_IMAGE` | `alpine:3.20` | Image for user containers |
| `CONTAINER_MEMORY` | `512m` | Docker memory and memory+swap limit |
| `CONTAINER_DISK` | `1g` | Hard writable-layer quota |
| `CONTAINER_CPUS` | `1.0` | Maximum CPU cores available to a user container |
| `CONTAINER_PIDS_LIMIT` | `128` | Maximum processes per container |
| `CONTAINER_NETWORK` | `alpine-filtered` | Internal Docker network created by Compose |
| `CONTAINER_HTTP_PROXY` | `http://172.30.0.2:3128` | Filtering proxy injected into user containers |
| `DATABASE_PATH` | `data/bot.db` | SQLite database path |

## Tests

```bash
python -m unittest discover -s tests -v
python -m compileall -q bot tests run.py
```

Docker integration testing requires access to the Docker socket and a quota-capable daemon.

## Project layout

```text
bot/config.py            Environment configuration
bot/db.py                Async SQLite persistence
bot/docker_manager.py    Container lifecycle and command execution
bot/terminal.py          Persistent TTY, ANSI screen renderer, and key input
bot/cogs/container.py    Discord slash commands and ownership checks
bot/main.py              Bot startup and command synchronization
network-filter/          Squid policy proxy and blocklist generator
blocklists/              Pinned upstream Git submodules
tests/                   Unit tests
```
