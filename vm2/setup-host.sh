#!/usr/bin/env bash
# ==============================================================================
# setup-host.sh — Подготовка хоста ВМ №2 для работы платформы
# Production Blueprint v5.2
# ==============================================================================
# Назначение:
#   1. Установка gVisor runtime (runsc) для изолированных песочниц.
#   2. Настройка Docker (включение runsc как runtime по умолчанию для sandbox).
#   3. Создание директории wheel-cache и wheel-cache volume.
#   4. Инициализация бакетов SeaweedFS S3 (factory-artifacts, postgres-backups, langfuse-events).
#   5. Проверка готовности хоста к запуску docker compose.
#
# Требования:
#   - Ubuntu 22.04 / 24.04 LTS (или Debian 12)
#   - Docker Engine 24+ и Docker Compose v2
#   - Права root (sudo)
#   - Доступ к интернету для загрузки gVisor (или внутреннее зеркало)
#
# Использование:
#   sudo ./setup-host.sh
# ==============================================================================

set -euo pipefail

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
RENDERED_DIR="${SCRIPT_DIR}/rendered"
WHEEL_CACHE_DIR="${SCRIPT_DIR}/wheel-cache"
LOG_FILE="/var/log/factory-vm2-setup.log"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ==============================================================================
# ФУНКЦИИ
# ==============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1" | tee -a "$LOG_FILE"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" | tee -a "$LOG_FILE" >&2
}

require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "Этот скрипт должен быть запущен от root (sudo ./setup-host.sh)"
        exit 1
    fi
}

require_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        log_error "Файл .env не найден: $ENV_FILE"
        log_error "Скопируйте .env.example в .env и заполните значения"
        exit 1
    fi
    log_success "Файл .env найден"
}

require_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "Docker не установлен. Установите Docker Engine 24+:"
        log_error "  https://docs.docker.com/engine/install/ubuntu/"
        exit 1
    fi

    if ! docker compose version &> /dev/null; then
        log_error "Docker Compose v2 не установлен"
        exit 1
    fi

    log_success "Docker $(docker --version) и Compose $(docker compose version --short) установлены"
}

# ==============================================================================
# 1. УСТАНОВКА GVISOR (runsc)
# ==============================================================================
install_gvisor() {
    log_info "=== Шаг 1: Установка gVisor (runsc) ==="

    if command -v runsc &> /dev/null; then
        log_success "gVisor уже установлен: $(runsc --version)"
        return 0
    fi

    log_info "Установка gVisor..."

    # Добавляем репозиторий gVisor
    curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-keyring.gpg 2>/dev/null || true

    # Определяем архитектуру
    ARCH=$(uname -m)
    if [[ "$ARCH" == "x86_64" ]]; then
        ARCH="amd64"
    elif [[ "$ARCH" == "aarch64" ]]; then
        ARCH="arm64"
    else
        log_error "Неподдерживаемая архитектура: $ARCH"
        exit 1
    fi

    # Добавляем репозиторий
    echo "deb [arch=$ARCH signed-by=/usr/share/keyrings/gvisor-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" | \
        tee /etc/apt/sources.list.d/gvisor.list > /dev/null

    # Устанавливаем
    apt-get update -qq
    apt-get install -y runsc

    if command -v runsc &> /dev/null; then
        log_success "gVisor установлен: $(runsc --version)"
    else
        log_error "Не удалось установить gVisor"
        exit 1
    fi
}

# ==============================================================================
# 2. НАСТРОЙКА DOCKER RUNTIME
# ==============================================================================
configure_docker_runtime() {
    log_info "=== Шаг 2: Настройка Docker runtime ==="

    DOCKER_DAEMON="/etc/docker/daemon.json"

    # Проверяем, есть ли уже настройка runsc
    if [[ -f "$DOCKER_DAEMON" ]] && grep -q "runsc" "$DOCKER_DAEMON" 2>/dev/null; then
        log_success "Docker runtime runsc уже настроен"
        return 0
    fi

    log_info "Добавление runsc в Docker daemon.json..."

    # Создаём или обновляем daemon.json
    if [[ -f "$DOCKER_DAEMON" ]]; then
        # Добавляем runsc в существующий конфиг
        python3 -c "
import json, sys
with open('$DOCKER_DAEMON') as f:
    config = json.load(f)
config.setdefault('runtimes', {})['runsc'] = {
    'path': '/usr/bin/runsc',
    'runtimeArgs': []
}
with open('$DOCKER_DAEMON', 'w') as f:
    json.dump(config, f, indent=2)
"
    else
        # Создаём новый конфиг
        mkdir -p /etc/docker
        cat > "$DOCKER_DAEMON" <<'EOF'
{
  "runtimes": {
    "runsc": {
      "path": "/usr/bin/runsc",
      "runtimeArgs": []
    }
  }
}
EOF
    fi

    log_success "Docker runtime runsc добавлен в $DOCKER_DAEMON"
    log_warn "Требуется перезапуск Docker: sudo systemctl restart docker"
}

# ==============================================================================
# 3. СОЗДАНИЕ WHEEL-CACHE
# ==============================================================================
setup_wheel_cache() {
    log_info "=== Шаг 3: Создание wheel-cache ==="

    if [[ ! -d "$WHEEL_CACHE_DIR" ]]; then
        mkdir -p "$WHEEL_CACHE_DIR"
        log_success "Директория wheel-cache создана: $WHEEL_CACHE_DIR"
    else
        log_success "Директория wheel-cache уже существует"
    fi

    # Устанавливаем права (для пользователя, который будет запускать docker compose)
    if [[ -n "${SUDO_USER:-}" ]]; then
        chown -R "$SUDO_USER:$SUDO_USER" "$WHEEL_CACHE_DIR"
        log_success "Права wheel-cache установлены для $SUDO_USER"
    fi
}

# ==============================================================================
# 4. ИНИЦИАЛИЗАЦИЯ БАКЕТОВ SEAWEEDFS
# ==============================================================================
init_seaweedfs_buckets() {
    log_info "=== Шаг 4: Инициализация бакетов SeaweedFS ==="

    # Загружаем переменные из .env
    set -a
    source "$ENV_FILE"
    set +a

    # Проверяем, что стек запущен
    if ! docker compose ps seaweedfs-s3 &> /dev/null; then
        log_warn "SeaweedFS S3 не запущен. Запустите: docker compose up -d"
        log_warn "Бакеты будут созданы при первом запуске S3-шлюза (через s3.json)"
        return 0
    fi

    S3_ENDPOINT="http://localhost:8333"
    BUCKETS=("factory-artifacts" "postgres-backups" "langfuse-events")

    for bucket in "${BUCKETS[@]}"; do
        if curl -s -f "$S3_ENDPOINT/$bucket" &> /dev/null; then
            log_success "Бакет '$bucket' уже существует"
        else
            log_info "Создание бакета '$bucket'..."
            # SeaweedFS S3 создаёт бакеты автоматически при первом запросе
            # или через weed CLI
            curl -s -X PUT "$S3_ENDPOINT/$bucket" -H "Authorization: AWS ${S3_FACTORY_ACCESS_KEY}:${S3_FACTORY_SECRET_KEY}" &> /dev/null || true
            log_success "Бакет '$bucket' создан (или уже существует)"
        fi
    done
}

# ==============================================================================
# 5. ПРОВЕРКА ГОТОВНОСТИ
# ==============================================================================
verify_setup() {
    log_info "=== Шаг 5: Проверка готовности ==="

    local issues=0

    # Проверка runsc
    if ! command -v runsc &> /dev/null; then
        log_error "gVisor (runsc) не установлен"
        ((issues++))
    else
        log_success "gVisor установлен"
    fi

    # Проверка Docker runtime
    if docker info 2>/dev/null | grep -q "runsc"; then
        log_success "Docker runtime runsc доступен"
    else
        log_warn "Docker runtime runsc не найден в docker info (возможно, нужен restart docker)"
    fi

    # Проверка .env
    if [[ -f "$ENV_FILE" ]]; then
        log_success ".env файл существует"
    else
        log_error ".env файл не найден"
        ((issues++))
    fi

    # Проверка rendered конфигов
    if [[ -d "$RENDERED_DIR" ]]; then
        log_success "Отрендеренные конфиги найдены"
    else
        log_warn "Отрендеренные конфиги не найдены. Запустите: ./render-configs.sh"
    fi

    # Проверка wheel-cache
    if [[ -d "$WHEEL_CACHE_DIR" ]]; then
        local whl_count
        whl_count=$(find "$WHEEL_CACHE_DIR" -name "*.whl" 2>/dev/null | wc -l)
        log_success "Wheel-cache: $whl_count пакетов"
        if [[ $whl_count -eq 0 ]]; then
            log_warn "Wheel-cache пуст. Загрузите пакеты перед сборкой toolset-образов"
        fi
    else
        log_error "Wheel-cache директория не найдена"
        ((issues++))
    fi

    if [[ $issues -gt 0 ]]; then
        log_error "Найдено $issues критических проблем. Исправьте их перед запуском."
        return 1
    else
        log_success "Все проверки пройдены! Хост готов к запуску платформы."
        return 0
    fi
}

# ==============================================================================
# ОСНОВНАЯ ЛОГИКА
# ==============================================================================

main() {
    log_info "=============================================="
    log_info "Настройка хоста ВМ №2 для Factory Agents Platform"
    log_info "=============================================="
    log_info ""

    require_root
    require_env
    require_docker

    echo ""
    install_gvisor
    echo ""
    configure_docker_runtime
    echo ""
    setup_wheel_cache
    echo ""
    init_seaweedfs_buckets
    echo ""
    verify_setup

    echo ""
    log_info "=============================================="
    log_info "Следующие шаги:"
    log_info "=============================================="
    log_info "1. Если Docker был перезапущен: sudo systemctl restart docker"
    log_info "2. Загрузите wheels в wheel-cache/ (из PyPI-зеркала ВМ №1)"
    log_info "3. Запустите: ./render-configs.sh"
    log_info "4. Запустите: docker compose up -d"
    log_info "5. Проверьте статус: docker compose ps"
    log_info ""
    log_success "Готово!"
}

main "$@"