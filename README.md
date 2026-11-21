# Alpine Container Discord Bot

A `discord.py` bot that gives each Discord user one personal Alpine Linux Docker container. A user runs `/givcontainerpls`; the bot creates a private control thread, adds that user, and starts a resource-limited container.

This is an initial implementation designed to be extended as requirements become clearer.

## Current behavior

- One container per Discord user **globally**, even if the bot is in several servers.
- Private control thread containing the bot and requesting user.
- A persistent `80×22` pseudo-terminal. Owner messages are deleted, forwarded to the TTY, and rendered into one edited rich embed by default, with a plain-message option.
- ANSI cursor movement and full-screen terminal programs are interpreted with `pyte` rather than posted as raw escape codes.
- The terminal code block is padded to exactly the configured rows and columns.
- TTYs disconnect after 10 minutes without input or output by default; the container stays running.
- Stateful message-component buttons for arrows, Ctrl, Alt, Esc, Tab, and sending key combinations.
- Owner-only slash controls, usable only in the linked thread:
  - `/settings [columns] [rows] [idle_minutes] [rich_embed] [refresh_seconds]`
  - `/send keys:<sequence>`
  - `/reset`
  - `/refresh`
  - `/showxorg`
  - `/showtty`
  - `/printtty`
  - `/status`
  - `/start`
  - `/stop`
  - `/restart`
  - `/destroycontainer`
- SQLite settings/ownership persistence and Docker writable-layer data persistence across bot and host reboots.
- Default Xorg-enabled Alpine image: `playaround-alpine-xorg:3.20`, built locally from Alpine 3.20.
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
- **Enter** sends a carriage return immediately, without needing a chord selection.
- **Xorg** switches to the headless graphical display without closing the TTY.
- Instead of pressing **Send**, type letters in the thread to combine them with queued buttons. Ctrl followed by `x` sends Ctrl+X; Ctrl+Alt followed by `ax` sends Ctrl+Alt+A and then `x`. Queued controls are cleared afterward and no Enter is appended.
- `/send keys:ctrl+alt+a+x` provides the same behavior when buttons are unavailable. Supported names are `ctrl`, `alt`, `esc`, `tab`, `up`, `down`, `left`, and `right`; other tokens are sent as text.
- `/settings columns:80 rows:22` resizes both the Docker TTY and terminal renderer.
- `/settings idle_minutes:10` changes the inactivity timeout per container; use `0` to disable automatic TTY disconnection.
- `/settings rich_embed:false refresh_seconds:3` uses a plain text TTY message and batches foreground output updates every three seconds. The refresh interval can be 1–30 seconds.
- `/refresh` redraws the current TTY message immediately, while `/printtty` deletes old TTY displays and moves the live one to the bottom of the thread.
- `/reset` clears the rendered screen and sends `reset` to the active TTY.
- `/restart` restarts the container, kills its current processes, and opens a fresh TTY without deleting its writable filesystem.

### Headless Xorg display

`/showxorg` hides and deletes the Discord TTY message without closing its Docker exec TTY, then starts an `800×600` Xvfb desktop inside the same user container. Xorg input counts as activity for the hidden TTY's idle timer. The Discord message is a rich embed whose image attachment is replaced after input or when **Refresh** is pressed. Xvfb listens only on the container's Unix socket (`:99`); no X11 TCP port is exposed.

The local user image includes Xvfb, Openbox, xterm, xdotool, and ImageMagick. A fresh desktop opens an xterm, and programs started with `DISPLAY=:99` appear in the image. The image includes a visible target drawn at the mouse position because ordinary X11 screenshots omit the pointer.

Xorg controls provide:

- four directional mouse buttons and selectable 100 px, 10 px, or 1 px movement;
- toggleable left/right mouse-button holds, allowing dragging before toggling the button up again;
- one-shot Ctrl and Alt modifiers;
- immediate Esc, Tab, and Enter keys, combined with queued Ctrl/Alt when selected;
- **Refresh** and **TTY** buttons.

While Xorg is visible, a normal thread message is typed into the focused X11 window and followed by Enter. If Ctrl or Alt is queued, the modifiers apply to the first ASCII letter/number, remaining text is typed literally, no Enter is appended, and the modifiers clear. `/showtty`, the **TTY** button, or `/printtty` deletes the Xorg image and restores the still-running terminal display. The **Xorg** button on the TTY message performs the reverse switch.

Screenshots are event-driven rather than video streaming to avoid Discord attachment and edit rate limits. Use **Refresh** for GUI changes that occur without an input event.

Only the container owner can operate its terminal buttons. Discord is not a low-latency transport, but programs such as editors, pagers, and process monitors can retain terminal state and react to input.

The TTY remains attached until its configured inactivity timeout, an explicit stop, or a bot restart. The next input opens a fresh shell in the same container; container files remain, but a foreground interactive program from the old TTY should be considered disconnected.

Every fresh TTY prints the container's current `/etc/motd`. This includes a TTY opened after its inactivity timeout and the fresh TTY opened by `/restart`.

### Container data persistence

User files are stored in each Docker container's writable layer, not in the bot container. Docker retains that layer across bot restarts and host reboots, and each user container has an `unless-stopped` restart policy. `/stop` stops processes without deleting files; `/start` and `/restart` reuse the same writable layer. Only `/destroycontainer` intentionally removes the container and its writable-layer data.

The ownership/settings database is stored at `./data/bot.db` by the Compose bind mount. Back up both Docker's data root and `./data` for disaster recovery. Docker data-root loss, manual `docker rm`, or container-pruning commands can still delete user data; this is persistence, not a backup.

## Hiding the server's public IP (optional WireGuard egress)

This project can send all user-container web traffic through a WireGuard VPN, so destination sites see the VPN exit IP instead of the host's public IP. It uses [Gluetun](https://github.com/qdm12/gluetun) with its built-in HTTP proxy and works with ProtonVPN or another Gluetun-supported provider.

The VPN is an **egress-only** layer:

- User containers still reach only Squid on the internal `alpine-filtered` network.
- Squid forwards every request to Gluetun's authenticated HTTP proxy.
- Gluetun sends that traffic through WireGuard.
- If WireGuard or Gluetun is unavailable, Squid's `never_direct allow all` rule fails closed instead of revealing the host IP.

### What this hides and what it does not

**Hidden from destination websites:** the host's public IPv4 address for HTTP/HTTPS traffic from user containers. Forwarding headers are already removed, and Gluetun's proxy does not add its own client-identifying headers in stealth mode.

**Not hidden from Discord:** the bot's Discord gateway connection uses the host's normal network path, so Discord can still see the host IP. A VPN daemon on the host could change that, but this Compose setup intentionally does not reroute the bot, Docker, SSH, or inbound services.

**DNS metadata:** destination sites see the VPN IP, but some hostname lookups can still reach the host's configured resolver. That can reveal the names being queried, though not the destination-visible public IP. Gluetun resolves proxied destination names through DNS-over-TLS inside the tunnel; Squid also performs local DNS lookups for its IP-based policy ACLs, and Docker's embedded resolver handles container-local lookups.

**Other traffic:** WireGuard covers web traffic through Squid, not arbitrary non-HTTP traffic, the bot's Discord connection, or host administration. It is not a full-host anonymization layer.

### ProtonVPN WireGuard setup

1. In the ProtonVPN dashboard, open **Downloads → WireGuard configuration** and create a configuration. Copy its `PrivateKey`.
2. Create the two local secret files:

```bash
cp vpn.env.example vpn.env
cp vpn-proxy.env.example vpn-proxy.env
```

3. Set the WireGuard key and exit location in `vpn.env`:

```dotenv
VPN_SERVICE_PROVIDER=protonvpn
VPN_TYPE=wireguard
WIREGUARD_PRIVATE_KEY=your-private-key
SERVER_COUNTRIES=Netherlands
```

4. Set a long random internal proxy password in `vpn-proxy.env`:

```dotenv
HTTPPROXY_USER=alpinevpn
HTTPPROXY_PASSWORD=use-a-long-random-value
```

Only letters, digits, `.`, `_`, `~`, and `-` are accepted in the internal proxy credentials. Keep both files private. `vpn.env` is mounted only into the VPN container; `network-filter` receives the proxy credentials but not the WireGuard private key. Both files are gitignored.

5. Start the stack with the VPN override:

```bash
docker compose -f compose.yaml -f compose.vpn.yaml up -d --build
```

6. Watch the tunnel and filter logs:

```bash
docker compose -f compose.yaml -f compose.vpn.yaml logs -f vpn network-filter
```

Gluetun should report a successful WireGuard handshake before `network-filter` becomes healthy. User containers created earlier do not need to be recreated: they already use Squid at `172.30.0.2:3128`; only Squid's upstream changes.

### Verify the exit IP

Compare the host's public IP with the VPN container's:

```bash
curl -fsS https://ifconfig.me/ip
docker compose -f compose.yaml -f compose.vpn.yaml exec vpn wget -qO- https://ifconfig.me/ip
```

The second command should show the VPN exit address, not the host address. To confirm fail-closed behavior, stop the VPN service and make a request through Squid; it should fail rather than fall back to the host:

```bash
docker compose -f compose.yaml -f compose.vpn.yaml stop vpn
```

Restart it afterward:

```bash
docker compose -f compose.yaml -f compose.vpn.yaml start vpn
```

Provider alternatives use the same `vpn.env` shape; change `VPN_SERVICE_PROVIDER`, `VPN_TYPE`, and the corresponding key fields. See Gluetun's provider documentation for provider-specific variables.

## Security warning

Docker isolation is not equivalent to a dedicated VM and should not be treated as a perfect hostile multi-tenant sandbox. Run this on a dedicated machine/VM, keep Docker and the kernel patched, and do not store secrets on that host. Mounting `/var/run/docker.sock` gives the **bot process** host-level Docker control, so protect the bot token and its host carefully.

User containers do not receive the Docker socket, host directories, extra capabilities, or a direct Internet route.

## Filtered network access

User containers join the `alpine-filtered` Docker network. That network is marked `internal`, so containers cannot route directly to the Internet even if a user unsets the proxy variables or changes application settings. The `network-filter` service is attached to both the internal network and a normal egress network and is the only web gateway.

The bot injects `HTTP_PROXY`, `HTTPS_PROXY`, and lowercase equivalents into each new container. With the default policy, Squid permits HTTP and HTTPS proxy traffic on ports 80 and 443 while denying:

- domains from [`elbkr/bad-websites`](https://github.com/elbkr/bad-websites);
- active phishing domains and IPs from [`Phishing-Database/Phishing.Database`](https://github.com/Phishing-Database/Phishing.Database);
- domains in `network-filter/custom-blocked-domains.txt`, including common public-IP lookup sites;
- literal IP destinations, which could otherwise bypass domain rules;
- non-HTTP protocols and unsafe destination ports.

Literal-IP filtering applies to proxy destinations such as `https://1.1.1.1`; it does **not** prevent normal DNS resolution. Containers use Docker's embedded resolver, and Squid resolves allowed hostnames on their behalf. Direct UDP/TCP requests to Google DNS (`8.8.8.8`) or Cloudflare DNS (`1.1.1.1`) have no route from the internal network. DNS-over-HTTPS by hostname can use the proxy unless that hostname is in a denied domain category.

### Policy switches

Edit `network-filter/policy.env` to set each category to `deny` or `allow`:

| Variable | Default | Controls |
|---|---|---|
| `POLICY_BAD_WEBSITES` | `deny` | Domains from `elbkr/bad-websites` |
| `POLICY_PHISHING_DOMAINS` | `deny` | Active Phishing.Database domains |
| `POLICY_CUSTOM_DOMAINS` | `deny` | `custom-blocked-domains.txt` |
| `POLICY_PHISHING_IPS` | `deny` | Active Phishing.Database IP addresses, including hostnames resolving to them |
| `POLICY_LITERAL_IPS` | `deny` | URLs/CONNECT destinations written as IPv4 or IPv6 literals |
| `POLICY_PRIVATE_IPS` | `deny` | Loopback, link-local, private, and reserved destination ranges |
| `POLICY_NON_HTTP` | `deny` | CONNECT tunnels to ports other than HTTPS port 443 |
| `POLICY_UNSAFE_PORTS` | `deny` | Destination ports outside Squid's safe-port set |

`allow` means “do not deny solely because of this category”; it is not an unconditional allowlist entry. A destination is still rejected if it matches another category that remains set to `deny`. For example, allowing literal IP destinations does not allow an address in the phishing-IP feed while `POLICY_PHISHING_IPS=deny`.

Apply policy changes by recreating the filter service so Compose reloads `policy.env`:

```bash
docker compose up -d --force-recreate network-filter
```

Allowing `POLICY_NON_HTTP` only permits CONNECT tunneling through Squid; it does not give containers a direct Internet route. `POLICY_UNSAFE_PORTS` continues to restrict those tunnels unless it is also allowed.

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

Edit `network-filter/custom-blocked-domains.txt` to add local policy entries. The filter regenerates its source-specific normalized Squid ACL files whenever the service starts.

### Filtering limitations

This is a denylist-based web policy, not a perfect hostile-user firewall. HTTPS hides URL paths, so filtering is by destination hostname/IP rather than full encrypted URL. A permitted site could proxy content from a blocked site, newly registered threats may not yet be listed, and blocklists can contain false positives. DNS and HTTPS can also be abused as tunneling channels by a determined attacker. For a strict environment, replace the denylist with a small explicit allowlist and run the Docker host in a dedicated VM.

Existing containers retain their creation-time network and environment settings. Destroy and recreate them after enabling this feature.

## Prerequisites

- Python 3.11 or newer
- Docker Engine
- A Discord application with a bot token
- A Docker storage driver/filesystem that supports per-container writable-layer `size` quotas

### Disk quota prerequisite

`CONTAINER_DISK` is a hard limit only when Docker's storage driver actually enforces per-container quotas. `overlay2` supports `--storage-opt size` only when its data root is on XFS mounted with project quotas (`pquota`/`prjquota`); btrfs and zfs also support quotas. On ext4/overlay2, Docker does not provide a writable-layer size clamp, and a container can fill the host disk even with `CONTAINER_DISK=1g`.

The bot verifies this at startup and remembers the result. Container creation is refused if the check failed.

- It first checks `docker info`; `overlay2` on a non-XFS backing filesystem is rejected immediately, as is Docker 29's containerd image store (`Storage Driver: overlayfs`) with instructions to disable it.
- It then creates a throwaway container with an 8 MiB writable-layer limit and writes 16 MiB to it.
- If the write succeeds, Docker did not enforce the quota. The bot logs the reason and refuses to create user containers.
- If Docker fails with `No space left on device`, the quota is working.

Check the daemon manually:

```bash
docker info | grep -E 'Storage Driver|Backing Filesystem|driver-type'
docker run --rm --storage-opt size=8m alpine:3.20 \
  sh -c 'dd if=/dev/zero of=/probe bs=1M count=16'
```

The first command must show `Storage Driver: overlay2` and `Backing Filesystem: xfs`, with no `driver-type: io.containerd.snapshotter.v1`. The second command must fail with `No space left on device`. If it succeeds, fix the host before using the bot.

#### Docker 29 and the containerd image store

Docker Engine 29 uses the containerd image store by default on fresh installs. It reports `Storage Driver: overlayfs` and `driver-type: io.containerd.snapshotter.v1`, ignores `--storage-opt size`, and keeps snapshots under `/var/lib/containerd`. An XFS mount at `/var/lib/docker` is therefore not in the container write path.

Switch back to the classic graph driver in `/etc/docker/daemon.json`:

```json
{
  "features": {
    "containerd-snapshotter": false
  },
  "storage-driver": "overlay2"
}
```

Merge these keys with any existing configuration, then:

```bash
sudo dockerd --validate --config-file=/etc/docker/daemon.json
sudo systemctl restart docker
docker info | grep -E 'Storage Driver|Backing Filesystem|driver-type'
```

The validation must print `configuration OK`, and `docker info` must show `overlay2` and `xfs` with no `driver-type` line. Switching storage backends hides images and containers created with the other backend; rebuild with `docker compose up -d --build`, then rerun the probe above.

#### Configure a quota-capable data root

This is a host-level change. Back up existing Docker data first; changing the data root makes existing images and containers inaccessible. A dedicated XFS partition or volume mounted at `/var/lib/docker` is the most reliable option. The mount must include project quotas:

```bash
# /etc/fstab example:
# UUID=<uuid> /var/lib/docker xfs defaults,pquota 0 0
sudo mount -o remount,pquota /var/lib/docker
```

For a disposable or test host, a loopback XFS image also works:

```bash
sudo systemctl stop docker
sudo truncate -s 200G /var/lib/docker-xfs.img
sudo mkfs.xfs -n ftype=1 /var/lib/docker-xfs.img
sudo mount -o loop,pquota /var/lib/docker-xfs.img /var/lib/docker
sudo systemctl start docker
```

Verify that `docker info` reports `Storage Driver: overlay2` and `Backing Filesystem: xfs`, then run the `--storage-opt size=8m` probe above. Add the mount to `/etc/fstab` so it survives reboots.

Existing containers keep the storage options they were created with. Recreate them after fixing the host; the bot cannot retroactively add a quota to a container created without one.

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
docker compose build user-image
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

To add the optional WireGuard egress, use the two-file Compose command described in **Hiding the server's public IP**:

```bash
docker compose -f compose.yaml -f compose.vpn.yaml up -d --build
```

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `DISCORD_TOKEN` | required | Discord bot token |
| `GUILD_ID` | unset | Test guild for immediate command sync; unset means global sync |
| `CONTAINER_IMAGE` | `playaround-alpine-xorg:3.20` | Locally built Alpine image with headless Xorg tools |
| `CONTAINER_MEMORY` | `512m` | Docker memory and memory+swap limit |
| `CONTAINER_DISK` | `1g` | Hard writable-layer quota; creation is refused unless the daemon's quota probe succeeds |
| `CONTAINER_CPUS` | `1.0` | Maximum CPU cores available to a user container |
| `CONTAINER_PIDS_LIMIT` | `128` | Maximum processes per container |
| `CONTAINER_NETWORK_ENABLED` | `true` | Set to `false` to run user containers with no Docker network and no proxy variables |
| `CONTAINER_NETWORK` | `alpine-filtered` | Internal Docker network created by Compose (ignored when networking is disabled) |
| `CONTAINER_HTTP_PROXY` | `http://172.30.0.2:3128` | Filtering proxy injected into user containers (ignored when networking is disabled) |
| `DATABASE_PATH` | `data/bot.db` | SQLite database path |
| `USER_DEFAULTS_PATH` | `config/user-defaults.toml` | TOML defaults copied into each new user's settings |

To completely disable networking for user containers, set this in `.env`:

```dotenv
CONTAINER_NETWORK_ENABLED=false
```

The bot then creates containers with Docker's `none` network mode and does not inject `HTTP_PROXY`/`HTTPS_PROXY`. Existing containers keep the network they were created with; recreate them to apply the change.

### New-user settings defaults

Edit `config/user-defaults.toml` to control the initial `/settings` values assigned when a user creates a container:

```toml
[terminal]
columns = 80
rows = 22
idle_minutes = 10
rich_embed = true
refresh_seconds = 3
```

The accepted ranges are the same as `/settings`: columns 20–120, rows 5–40, idle timeout 0–1440 minutes, and refresh interval 1–30 seconds. The terminal area must not exceed 1760 characters. Invalid files fail startup with a descriptive error.

These are creation-time defaults. Changing the file does not overwrite existing users' saved choices. Recreate the bot service to load an edited file:

```bash
docker compose up -d --force-recreate bot
```

### User container image

`docker compose up --build -d` builds `user-image/Dockerfile` as `playaround-alpine-xorg:3.20` before the bot starts. For a non-Compose bot process, build it first:

```bash
docker compose build user-image
```

Containers created by older versions from plain `alpine:3.20` do not contain the Xorg tools. They continue to work as terminal containers, but `/showxorg` reports the missing tool. Back up their files and recreate them if Xorg access is required; changing `CONTAINER_IMAGE` never silently replaces an existing user's persistent container.

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
bot/xorg.py              Xvfb screenshot renderer and GUI input controls
bot/cogs/container.py    Discord slash commands and ownership checks
bot/main.py              Bot startup and command synchronization
config/user-defaults.toml  Initial per-user terminal settings
user-image/              Alpine user image with Xvfb/Openbox/xterm
compose.vpn.yaml         Optional Gluetun/WireGuard egress override
vpn.env.example          WireGuard provider/private-key template
vpn-proxy.env.example    Internal Squid-to-Gluetun proxy credentials template
network-filter/          Squid policy proxy and blocklist generator
blocklists/              Pinned upstream Git submodules
tests/                   Unit tests
```
