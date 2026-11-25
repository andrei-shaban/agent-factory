#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
WHEEL_CACHE_DIR="${SCRIPT_DIR}/wheel-cache"

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

mkdir -p "$WHEEL_CACHE_DIR"
if [[ -n "${SUDO_USER:-}" ]]; then
    chown -R "$SUDO_USER:$SUDO_USER" "$WHEEL_CACHE_DIR"
fi

if [[ "$sandbox_ready" != true ]]; then
    log WARN "Regular Compose services may run, but sandbox execution remains blocked until a dedicated rootless dockerd with runsc is configured."
fi
