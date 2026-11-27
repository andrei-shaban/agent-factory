#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

log() { printf '[%s] %s\n' "$1" "$2"; }

if [[ ! -f "$ENV_FILE" ]]; then
    log ERROR "Missing ${ENV_FILE}; copy .env.example and configure host-specific values."
    exit 1
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    log ERROR "Docker Engine and Docker Compose are required on the VM2 host."
    exit 1
fi

if command -v runsc >/dev/null 2>&1; then
    log INFO "Found gVisor runtime: $(runsc --version 2>&1 | head -n 1)"
else
    log WARN "runsc is not installed; sandbox execution is unavailable."
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# ==============================================================================
# E3/E6: Создание хостовых каталогов для bind-mount
# ==============================================================================
WHEEL_CACHE_DIR="${WHEEL_CACHE_HOST_PATH:-/srv/factory/wheel-cache}"
RUNS_DIR="${SANDBOX_RUNS_HOST_PATH:-/srv/factory/runs}"

log INFO "Creating host directories for bind-mount..."
mkdir -p "$WHEEL_CACHE_DIR" "$RUNS_DIR"

# Владелец: uid 10001 (factory) — пользователь воркера
if [[ -n "${SUDO_USER:-}" ]]; then
    chown -R 10001:10001 "$WHEEL_CACHE_DIR" "$RUNS_DIR"
    chmod -R 755 "$WHEEL_CACHE_DIR" "$RUNS_DIR"
    log INFO "Directories created with owner 10001 (factory): ${WHEEL_CACHE_DIR}, ${RUNS_DIR}"
else
    log WARN "Not running as sudo; ensure ${WHEEL_CACHE_DIR} and ${RUNS_DIR} are owned by uid 10001"
fi

# ==============================================================================
# Проверка готовности sandbox-демона
# ==============================================================================
sandbox_ready=false
if [[ -n "${SANDBOX_DOCKER_SOCKET:-}" && -S "${SANDBOX_DOCKER_SOCKET}" ]]; then
    if runtimes=$(DOCKER_HOST="unix://${SANDBOX_DOCKER_SOCKET}" docker info --format '{{json .Runtimes}}' 2>/dev/null) && [[ "$runtimes" == *'runsc'* ]]; then
        sandbox_ready=true
        log INFO "Dedicated rootless sandbox dockerd and runsc runtime are available."
    else
        log WARN "Dedicated sandbox socket is not responding with runsc enabled."
    fi
else
    log WARN "Dedicated rootless sandbox socket is unavailable; the main Docker daemon will not be used."
fi

if [[ "$sandbox_ready" != true ]]; then
    log WARN "Regular Compose services may run, but sandbox execution remains blocked until a dedicated rootless dockerd with runsc is configured."
fi

log INFO "Host setup completed."