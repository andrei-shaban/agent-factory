#!/usr/bin/env bash
# Provision a dedicated rootless Docker daemon for VM2 sandbox workloads.
# Run on the actual Linux VM2 host: sudo ./setup-rootless-sandbox.sh
# This script never edits the primary Docker daemon configuration or mounts its socket.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

log() { printf '[%s] %s\n' "$1" "$2"; }
die() { log ERROR "$1"; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "Run with sudo on the actual Linux VM2 host."
[[ -f "$ENV_FILE" ]] || die "Missing ${ENV_FILE}; configure VM2 .env first."
[[ -x "$(command -v docker || true)" ]] || die "Docker CLI is required on VM2."
command -v apt-get >/dev/null 2>&1 || die "This bootstrap currently supports Debian/Ubuntu VM2 hosts only."

SANDBOX_USER="${SUDO_USER:-}"
[[ -n "$SANDBOX_USER" && "$SANDBOX_USER" != root ]] || die "SUDO_USER must identify the non-root VM2 operator account."
id "$SANDBOX_USER" >/dev/null 2>&1 || die "Unknown operator account: ${SANDBOX_USER}"
SANDBOX_UID="$(id -u "$SANDBOX_USER")"
(( SANDBOX_UID > 0 )) || die "The sandbox daemon must run as a non-root user."
grep -q "^${SANDBOX_USER}:" /etc/subuid 2>/dev/null || die "Configure a unique subordinate UID range for ${SANDBOX_USER} in /etc/subuid before setup."
grep -q "^${SANDBOX_USER}:" /etc/subgid 2>/dev/null || die "Configure a unique subordinate GID range for ${SANDBOX_USER} in /etc/subgid before setup."
SANDBOX_HOME="$(getent passwd "$SANDBOX_USER" | cut -d: -f6)"
[[ -n "$SANDBOX_HOME" && -d "$SANDBOX_HOME" ]] || die "Could not resolve home directory for ${SANDBOX_USER}."
RUNTIME_DIR="/run/user/${SANDBOX_UID}"
SANDBOX_SOCKET="${RUNTIME_DIR}/docker.sock"

# Never accept the primary/rootful daemon socket as the sandbox socket.
[[ "$SANDBOX_SOCKET" != /var/run/docker.sock ]] || die "Refusing to use the primary Docker socket."

log INFO "Installing rootless prerequisites and gVisor on VM2."
apt-get update -qq
apt-get install -y uidmap dbus-user-session slirp4netns fuse-overlayfs curl ca-certificates gnupg

if ! command -v runsc >/dev/null 2>&1; then
    install -d -m 0755 /usr/share/keyrings
    curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor --yes -o /usr/share/keyrings/gvisor-archive-keyring.gpg
    arch="$(dpkg --print-architecture)"
    printf 'deb [arch=%s signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main\n' "$arch" \
        > /etc/apt/sources.list.d/gvisor.list
    apt-get update -qq
    apt-get install -y runsc
fi
RUNSC_PATH="$(command -v runsc)"

if ! command -v dockerd-rootless-setuptool.sh >/dev/null 2>&1; then
    log INFO "Installing Docker's rootless daemon support package."
    apt-get install -y docker-ce-rootless-extras || die "Install docker-ce-rootless-extras from Docker's Engine repository, then rerun this script."
fi
command -v dockerd-rootless-setuptool.sh >/dev/null 2>&1 || die "dockerd-rootless-setuptool.sh is unavailable after package installation."

install -d -m 0700 -o "$SANDBOX_USER" -g "$(id -gn "$SANDBOX_USER")" "${SANDBOX_HOME}/.config/docker"
DAEMON_CONFIG="${SANDBOX_HOME}/.config/docker/daemon.json"
if [[ -f "$DAEMON_CONFIG" ]]; then
    cp -a "$DAEMON_CONFIG" "${DAEMON_CONFIG}.bak.$(date +%Y%m%d%H%M%S)"
fi
python3 - "$DAEMON_CONFIG" "$RUNSC_PATH" <<'PY'
import json
import os
import sys

path, runsc = sys.argv[1:]
try:
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
except FileNotFoundError:
    config = {}
except json.JSONDecodeError as exc:
    raise SystemExit(f"Invalid existing rootless daemon config {path}: {exc}")

runtimes = config.setdefault("runtimes", {})
runtimes["runsc"] = {"path": runsc, "runtimeArgs": []}
config["default-runtime"] = "runsc"
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
chown "$SANDBOX_USER:$(id -gn "$SANDBOX_USER")" "$DAEMON_CONFIG"

loginctl enable-linger "$SANDBOX_USER"
systemctl start "user@${SANDBOX_UID}.service"
export XDG_RUNTIME_DIR="$RUNTIME_DIR"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${RUNTIME_DIR}/bus"

if ! runuser -u "$SANDBOX_USER" -- env HOME="$SANDBOX_HOME" XDG_RUNTIME_DIR="$RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" PATH="/usr/local/bin:/usr/bin:/bin" \
    systemctl --user cat docker.service >/dev/null 2>&1; then
    runuser -u "$SANDBOX_USER" -- env HOME="$SANDBOX_HOME" XDG_RUNTIME_DIR="$RUNTIME_DIR" \
        DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" PATH="/usr/local/bin:/usr/bin:/bin" \
        dockerd-rootless-setuptool.sh install
fi

runuser -u "$SANDBOX_USER" -- env HOME="$SANDBOX_HOME" XDG_RUNTIME_DIR="$RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" PATH="/usr/local/bin:/usr/bin:/bin" \
    systemctl --user daemon-reload
runuser -u "$SANDBOX_USER" -- env HOME="$SANDBOX_HOME" XDG_RUNTIME_DIR="$RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" PATH="/usr/local/bin:/usr/bin:/bin" \
    systemctl --user enable --now docker.service
runuser -u "$SANDBOX_USER" -- env HOME="$SANDBOX_HOME" XDG_RUNTIME_DIR="$RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" PATH="/usr/local/bin:/usr/bin:/bin" \
    systemctl --user restart docker.service

[[ -S "$SANDBOX_SOCKET" ]] || die "Rootless daemon did not create ${SANDBOX_SOCKET}."

sandbox_info="$(runuser -u "$SANDBOX_USER" -- env HOME="$SANDBOX_HOME" XDG_RUNTIME_DIR="$RUNTIME_DIR" \
    DOCKER_HOST="unix://${SANDBOX_SOCKET}" PATH="/usr/local/bin:/usr/bin:/bin" \
    docker info --format '{{json .SecurityOptions}}|{{json .Runtimes}}|{{.DockerRootDir}}')" || die "Could not query dedicated rootless daemon."
[[ "$sandbox_info" == *rootless* ]] || die "Docker info did not identify this daemon as rootless."
[[ "$sandbox_info" == *runsc* ]] || die "Dedicated rootless daemon does not advertise runsc."

# Update only the two sandbox endpoint keys after the isolated daemon is verified.
python3 - "$ENV_FILE" "$SANDBOX_SOCKET" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
socket_path = sys.argv[2]
updates = {
    "SANDBOX_DOCKER_SOCKET": socket_path,
    "SANDBOX_DOCKER_HOST": "tcp://docker-socket-proxy:2375",
}
lines = path.read_text(encoding="utf-8-sig").splitlines()
seen = set()
result = []
for line in lines:
    stripped = line.strip()
    if stripped and not stripped.startswith("#") and "=" in stripped:
        key = stripped.split("=", 1)[0]
        if key in updates:
            if key not in seen:
                result.append(f"{key}='{updates[key]}'")
                seen.add(key)
            continue
    result.append(line)
for key, value in updates.items():
    if key not in seen:
        result.append(f"{key}='{value}'")
path.write_text("\n".join(result) + "\n", encoding="utf-8")
PY

# Start only the optional proxy + worker on the primary VM2 engine. The proxy bind-mounts
# the dedicated rootless socket above, never /var/run/docker.sock from the primary daemon.
unset DOCKER_HOST DOCKER_CONTEXT
cd "$SCRIPT_DIR"
docker compose --profile sandbox config -q
docker compose --profile sandbox up -d docker-socket-proxy worker

proxy_mount="$(docker inspect vm2-docker-socket-proxy --format '{{range .Mounts}}{{.Source}}=>{{.Destination}}{{end}}')"
[[ "$proxy_mount" == "${SANDBOX_SOCKET}=>/var/run/docker.sock" ]] || die "Socket proxy mount does not match the dedicated rootless socket: ${proxy_mount}"
[[ "$proxy_mount" != /var/run/docker.sock\=* ]] || die "Refusing a proxy mount from the primary Docker socket."

log INFO "Dedicated rootless sandbox daemon, runsc default runtime, and optional proxy are configured."
printf 'Sandbox socket: %s\nProxy endpoint: %s\n' "$SANDBOX_SOCKET" 'tcp://docker-socket-proxy:2375'
