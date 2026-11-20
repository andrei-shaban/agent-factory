#!/usr/bin/env bash
# ==============================================================================
# deploy.sh — Развёртывание Factory Agents Platform
# Production Blueprint v5.1
# ==============================================================================
# Назначение:
#   - Загрузка Docker-образов из air-gap tar-архивов
#   - Рендеринг шаблонных конфигов (envsubst)
#   - Деплой стека на ВМ №1 и/или ВМ №2 с проверкой health
#   - Откат к предыдущей версии при ошибке
#   - Просмотр статуса развёрнутых сервисов
#
# Использование (на каждой ВМ свой экземпляр скрипта):
#   ./scripts/deploy.sh load-images --dir /opt/factory/images
#   ./scripts/deploy.sh deploy --version 1.0.0 --vm vm2
#   ./scripts/deploy.sh rollback --vm vm2
#   ./scripts/deploy.sh status --vm vm2
#
# Требования:
#   - Docker Engine 24+, Docker Compose v2
#   - envsubst (пакет gettext)
#   - Права пользователя в группе docker (или root)
#   - Для ВМ №2: gVisor (runsc) должен быть установлен заранее (setup-host.sh)
# ==============================================================================

set -euo pipefail

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.yml"
IMAGES_DIR="/opt/factory/images"
BACKUP_DIR="/opt/factory/image-backups"
LOG_FILE="/var/log/factory-deploy.log"
DEPLOY_STATE_FILE="/opt/factory/.deploy-state"

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# ==============================================================================
# ЛОГИРОВАНИЕ
# ==============================================================================
log_info()    { echo -e "${BLUE}[INFO]$(date '+%H:%M:%S')${NC} $1" | tee -a "$LOG_FILE"; }
log_success() { echo -e "${GREEN}[OK]$(date '+%H:%M:%S')${NC} $1" | tee -a "$LOG_FILE"; }
log_warn()    { echo -e "${YELLOW}[WARN]$(date '+%H:%M:%S')${NC} $1" | tee -a "$LOG_FILE"; }
log_error()   { echo -e "${RED}[ERROR]$(date '+%H:%M:%S')${NC} $1" | tee -a "$LOG_FILE" >&2; }
log_step()    { echo -e "\n${CYAN}==> $1${NC}" | tee -a "$LOG_FILE"; }

# ==============================================================================
# ПРОВЕРКИ
# ==============================================================================
require_docker() {
    if ! command -v docker &>/dev/null; then
        log_error "Docker не установлен"
        exit 1
    fi
    if ! docker compose version &>/dev/null; then
        log_error "Docker Compose v2 не установлен"
        exit 1
    fi
}

require_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        log_error ".env не найден: $ENV_FILE"
        log_error "Скопируйте .env.example в .env и заполните"
        exit 1
    fi
}

require_compose_file() {
    if [[ ! -f "$COMPOSE_FILE" ]]; then
        log_error "docker-compose.yml не найден: $COMPOSE_FILE"
        exit 1
    fi
}

require_envsubst() {
    if ! command -v envsubst &>/dev/null; then
        log_error "envsubst не найден. Установите: sudo apt-get install gettext"
        exit 1
    fi
}

# ==============================================================================
# УПРАВЛЕНИЕ СОСТОЯНИЕМ ДЕПЛОЯ
# ==============================================================================
save_deploy_state() {
    local version="$1"
    local timestamp
    timestamp=$(date -Iseconds)
    mkdir -p "$(dirname "$DEPLOY_STATE_FILE")"
    cat > "$DEPLOY_STATE_FILE" <<EOF
version=$version
timestamp=$timestamp
project_dir=$PROJECT_DIR
EOF
    log_info "Состояние деплоя сохранено: v${version} @ ${timestamp}"
}

load_deploy_state() {
    if [[ ! -f "$DEPLOY_STATE_FILE" ]]; then
        log_error "Нет сохранённого состояния деплоя (невозможно сделать rollback)"
        exit 1
    fi
    # shellcheck source=/dev/null
    source "$DEPLOY_STATE_FILE"
}

# ==============================================================================
# 1. ЗАГРУЗКА ОБРАЗОВ (AIR-GAP)
# ==============================================================================
cmd_load_images() {
    local dir="$IMAGES_DIR"

    # Парсинг аргументов
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dir) dir="$2"; shift 2 ;;
            *) log_error "Неизвестный аргумент: $1"; exit 1 ;;
        esac
    done

    log_step "Загрузка Docker-образов из $dir"

    if [[ ! -d "$dir" ]]; then
        log_error "Директория с образами не найдена: $dir"
        exit 1
    fi

    local count=0
    for tar in "$dir"/*.tar "$dir"/*.tar.gz; do
        [[ -f "$tar" ]] || continue
        log_info "Загрузка: $(basename "$tar")"
        docker load -i "$tar" 2>&1 | tee -a "$LOG_FILE"
        ((count++))
    done

    if [[ $count -eq 0 ]]; then
        log_warn "Tar-архивы не найдены в $dir"
    else
        log_success "Загружено образов: $count"
    fi
}

# ==============================================================================
# 2. РЕНДЕРИНГ КОНФИГОВ
# ==============================================================================
cmd_render_configs() {
    log_step "Рендеринг конфигурационных файлов"
    require_envsubst
    require_env

    if [[ -x "${PROJECT_DIR}/render-configs.sh" ]]; then
        "${PROJECT_DIR}/render-configs.sh"
    else
        log_warn "render-configs.sh не найден или не исполняемый — пропускаем"
    fi
}

# ==============================================================================
# 3. ДЕПЛОЙ
# ==============================================================================
cmd_deploy() {
    local version=""
    local skip_render=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --version) version="$2"; shift 2 ;;
            --skip-render) skip_render=true; shift ;;
            *) log_error "Неизвестный аргумент: $1"; exit 1 ;;
        esac
    done

    require_docker
    require_env
    require_compose_file

    if [[ -z "$version" ]]; then
        log_error "Требуется --version <тег>"
        exit 1
    fi

    log_step "Деплой версии $version"

    # 1. Сохраняем текущие образы для возможного rollback
    log_info "Сохранение текущих образов для rollback..."
    mkdir -p "$BACKUP_DIR"
    local backup_timestamp
    backup_timestamp=$(date +%Y%m%d_%H%M%S)

    # Получаем список образов из docker-compose
    local images
    images=$(docker compose -f "$COMPOSE_FILE" config --images 2>/dev/null | grep -v '^$' || true)

    if [[ -n "$images" ]]; then
        for img in $images; do
            local img_name
            img_name=$(echo "$img" | sed 's|/|_|g; s|:|_|g')
            local backup_file="${BACKUP_DIR}/${img_name}_${backup_timestamp}.tar"
            log_info "Бэкап образа: $img -> $backup_file"
            docker save -o "$backup_file" "$img" 2>/dev/null || log_warn "Не удалось сохранить $img"
        done
    fi

    # 2. Рендеринг конфигов (если не пропущен)
    if [[ "$skip_render" == false ]]; then
        cmd_render_configs
    fi

    # 3. Остановка старых контейнеров (graceful)
    log_info "Остановка старых контейнеров..."
    docker compose -f "$COMPOSE_FILE" down --remove-orphans 2>&1 | tee -a "$LOG_FILE" || true

    # 4. Запуск нового стека
    log_info "Запуск стека версии $version..."
    docker compose -f "$COMPOSE_FILE" up -d 2>&1 | tee -a "$LOG_FILE"

    if [[ $? -ne 0 ]]; then
        log_error "Не удалось запустить стек. Запустите: ./scripts/deploy.sh rollback"
        exit 1
    fi

    # 5. Проверка health
    log_info "Ожидание готовности сервисов (до 120 секунд)..."
    local ready=true
    for i in {1..24}; do
        local unhealthy
        unhealthy=$(docker compose -f "$COMPOSE_FILE" ps --format '{{.Name}} {{.Status}}' 2>/dev/null \
            | grep -i "unhealthy" | wc -l)
        if [[ $unhealthy -eq 0 ]]; then
            ready=true
            break
        fi
        ready=false
        sleep 5
    done

    if [[ "$ready" == true ]]; then
        log_success "Все сервисы запущены и готовы"
    else
        log_warn "Некоторые сервисы ещё не прошли healthcheck (проверьте: docker compose ps)"
    fi

    # 6. Сохраняем состояние
    save_deploy_state "$version"

    log_step "Деплой версии $version завершён"
    cmd_status
}

# ==============================================================================
# 4. ROLLBACK
# ==============================================================================
cmd_rollback() {
    require_docker
    require_compose_file

    log_step "Откат к предыдущей версии"

    load_deploy_state
    log_info "Текущая версия для отката: $version (задеплоена $timestamp)"

    # Восстанавливаем образы из последнего бэкапа
    if [[ -d "$BACKUP_DIR" ]]; then
        log_info "Восстановление образов из бэкапов..."
        for tar in "$BACKUP_DIR"/*.tar; do
            [[ -f "$tar" ]] || continue
            log_info "Загрузка: $(basename "$tar")"
            docker load -i "$tar" 2>&1 | tee -a "$LOG_FILE"
        done
    else
        log_warn "Бэкапы не найдены в $BACKUP_DIR"
    fi

    # Перезапуск стека
    log_info "Перезапуск стека..."
    docker compose -f "$COMPOSE_FILE" down --remove-orphans 2>&1 | tee -a "$LOG_FILE" || true
    docker compose -f "$COMPOSE_FILE" up -d 2>&1 | tee -a "$LOG_FILE"

    # Удаляем запись о деплое (откат выполнен)
    rm -f "$DEPLOY_STATE_FILE"

    log_success "Откат выполнен"
    cmd_status
}

# ==============================================================================
# 5. СТАТУС
# ==============================================================================
cmd_status() {
    log_step "Статус развёрнутых сервисов"

    if [[ -f "$DEPLOY_STATE_FILE" ]]; then
        load_deploy_state
        echo -e "${CYAN}Задеплоенная версия:${NC} $version (от $timestamp)"
    else
        echo -e "${YELLOW}Состояние деплоя не записано${NC}"
    fi

    echo ""
    if [[ -f "$COMPOSE_FILE" ]]; then
        docker compose -f "$COMPOSE_FILE" ps
    else
        log_warn "docker-compose.yml не найден"
    fi

    echo ""
    log_info "Последние 20 строк логов:"
    if [[ -f "$LOG_FILE" ]]; then
        tail -n 20 "$LOG_FILE"
    fi
}

# ==============================================================================
# СПРАВКА
# ==============================================================================
usage() {
    cat <<EOF
${CYAN}Factory Agents Platform — Deploy Script${NC}

Использование:
  $0 <command> [options]

Команды:
  load-images [--dir <path>]     Загрузить Docker-образы из tar-архивов
  deploy --version <тег>         Развернуть новую версию (с бэкапом и healthcheck)
  rollback                       Откат к предыдущей версии
  status                         Показать статус развёрнутых сервисов
  render-configs                 Отрендерить шаблонные конфиги
  help                           Показать эту справку

Примеры:
  $0 load-images --dir /opt/factory/images
  $0 deploy --version 1.2.3
  $0 deploy --version 1.2.3 --skip-render
  $0 rollback
  $0 status

Файлы:
  Состояние деплоя:  $DEPLOY_STATE_FILE
  Бэкапы образов:    $BACKUP_DIR
  Лог:               $LOG_FILE
EOF
}

# ==============================================================================
# MAIN
# ==============================================================================
main() {
    mkdir -p "$(dirname "$LOG_FILE")"
    log_info "=== deploy.sh запущен: $* ==="

    if [[ $# -eq 0 ]]; then
        usage
        exit 0
    fi

    local command="$1"
    shift

    case "$command" in
        load-images)    cmd_load_images "$@" ;;
        deploy)         cmd_deploy "$@" ;;
        rollback)       cmd_rollback "$@" ;;
        status)         cmd_status "$@" ;;
        render-configs) cmd_render_configs "$@" ;;
        help|--help|-h) usage ;;
        *)
            log_error "Неизвестная команда: $command"
            usage
            exit 1
            ;;
    esac
}

main "$@"