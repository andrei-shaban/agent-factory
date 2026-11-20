#!/usr/bin/env bash
# ==============================================================================
# render-configs.sh — Рендеринг конфигурационных файлов для ВМ №2
# Production Blueprint v5.1
# ==============================================================================
# Назначение: Подстановка переменных окружения из .env в шаблонные конфиги,
# которые не поддерживают переменные окружения нативно (Redis, SeaweedFS S3).
#
# Использование:
#   1. Убедитесь, что .env заполнен (cp .env.example .env && nano .env)
#   2. Запустите скрипт: ./render-configs.sh
#   3. Запустите стек: docker compose up -d
#
# Результат: Создаёт директорию rendered/ с готовыми конфигами.
#            docker-compose.yml должен монтировать файлы из rendered/, а не оригиналы.
# ==============================================================================

set -euo pipefail

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
RENDERED_DIR="${SCRIPT_DIR}/rendered"

# Список файлов для рендеринга (относительные пути от SCRIPT_DIR)
FILES_TO_RENDER=(
    "redis/redis.conf"
    "redis/acl.conf"
    "seaweedfs/s3.json"
)

# Обязательные переменные (проверка перед рендерингом)
REQUIRED_VARS=(
    "REDIS_FACTORY_PASSWORD"
    "REDIS_FACTORY_ACL_PASSWORD"
    "S3_ACCESS_KEY"
    "S3_SECRET_KEY"
    "S3_FACTORY_ACCESS_KEY"
    "S3_FACTORY_SECRET_KEY"
)

# ==============================================================================
# ФУНКЦИИ
# ==============================================================================

log_info() {
    echo "[INFO] $1"
}

log_error() {
    echo "[ERROR] $1" >&2
}

log_success() {
    echo "[OK] $1"
}

# Загрузка переменных из .env
load_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        log_error "Файл .env не найден: $ENV_FILE"
        log_error "Скопируйте .env.example в .env и заполните значения"
        exit 1
    fi

    log_info "Загрузка переменных из $ENV_FILE"

    # Экспортируем все переменные из .env (кроме комментариев и пустых строк)
    set -a
    source "$ENV_FILE"
    set +a
}

# Проверка наличия обязательных переменных
check_required_vars() {
    log_info "Проверка обязательных переменных..."

    local missing_vars=()

    for var in "${REQUIRED_VARS[@]}"; do
        if [[ -z "${!var:-}" ]]; then
            missing_vars+=("$var")
        fi
    done

    if [[ ${#missing_vars[@]} -gt 0 ]]; then
        log_error "Отсутствуют обязательные переменные в .env:"
        for var in "${missing_vars[@]}"; do
            log_error "  - $var"
        done
        exit 1
    fi

    log_success "Все обязательные переменные установлены"
}

# Проверка наличия envsubst
check_envsubst() {
    if ! command -v envsubst &> /dev/null; then
        log_error "envsubst не найден. Установите gettext:"
        log_error "  Ubuntu/Debian: sudo apt-get install gettext"
        log_error "  CentOS/RHEL: sudo yum install gettext"
        log_error "  macOS: brew install gettext"
        exit 1
    fi

    log_success "envsubst найден: $(command -v envsubst)"
}

# Рендеринг одного файла
render_file() {
    local src_file="$1"
    local src_path="${SCRIPT_DIR}/${src_file}"
    local dst_path="${RENDERED_DIR}/${src_file}"
    local dst_dir
    dst_dir="$(dirname "$dst_path")"

    if [[ ! -f "$src_path" ]]; then
        log_error "Исходный файл не найден: $src_path"
        return 1
    fi

    # Создаём директорию назначения
    mkdir -p "$dst_dir"

    # Рендеринг через envsubst
    # ${VAR} заменяется на значение переменной окружения
    envsubst < "$src_path" > "$dst_path"

    log_success "Отрендерен: $src_file -> rendered/$src_file"
}

# Очистка старых rendered файлов
clean_rendered() {
    if [[ -d "$RENDERED_DIR" ]]; then
        log_info "Очистка старой директории rendered/"
        rm -rf "$RENDERED_DIR"
    fi

    mkdir -p "$RENDERED_DIR"
}

# Вывод инструкции по изменению docker-compose.yml
print_compose_instructions() {
    log_info ""
    log_info "================================================================"
    log_info "ВАЖНО: Измените docker-compose.yml для использования rendered файлов"
    log_info "================================================================"
    log_info ""
    log_info "Замените монтирования конфигов:"
    log_info ""
    log_info "  redis-factory:"
    log_info "    volumes:"
    log_info "      - ./redis/redis.conf:/usr/local/etc/redis/redis.conf:ro"
    log_info "      - ./redis/acl.conf:/usr/local/etc/redis/acl.conf:ro"
    log_info ""
    log_info "На:"
    log_info ""
    log_info "  redis-factory:"
    log_info "    volumes:"
    log_info "      - ./rendered/redis/redis.conf:/usr/local/etc/redis/redis.conf:ro"
    log_info "      - ./rendered/redis/acl.conf:/usr/local/etc/redis/acl.conf:ro"
    log_info ""
    log_info "И для seaweedfs-s3:"
    log_info ""
    log_info "  seaweedfs-s3:"
    log_info "    volumes:"
    log_info "      - ./seaweedfs/s3.json:/etc/seaweedfs/s3.json:ro"
    log_info ""
    log_info "На:"
    log_info ""
    log_info "  seaweedfs-s3:"
    log_info "    volumes:"
    log_info "      - ./rendered/seaweedfs/s3.json:/etc/seaweedfs/s3.json:ro"
    log_info ""
    log_info "================================================================"
}

# ==============================================================================
# ОСНОВНАЯ ЛОГИКА
# ==============================================================================

main() {
    log_info "Начало рендеринга конфигурационных файлов для ВМ №2"
    log_info "Директория проекта: $SCRIPT_DIR"
    log_info ""

    # Загрузка .env
    load_env

    # Проверки
    check_required_vars
    check_envsubst

    # Очистка старых rendered файлов
    clean_rendered

    # Рендеринг каждого файла
    log_info ""
    log_info "Рендеринг файлов..."

    local success_count=0
    local fail_count=0

    for file in "${FILES_TO_RENDER[@]}"; do
        if render_file "$file"; then
            ((success_count++))
        else
            ((fail_count++))
        fi
    done

    log_info ""
    log_info "================================================================"
    log_info "Результат: $success_count успешно, $fail_count ошибок"
    log_info "================================================================"

    if [[ $fail_count -gt 0 ]]; then
        log_error "Некоторые файлы не удалось отрендерить"
        exit 1
    fi

    # Инструкция по docker-compose
    print_compose_instructions

    log_success "Готово! Теперь можно запускать: docker compose up -d"
}

# Запуск
main "$@"