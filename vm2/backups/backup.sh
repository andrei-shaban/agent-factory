# vm2/backups/backup.sh
#!/usr/bin/env bash
# ==============================================================================
# backup.sh — Автоматический бэкап PostgreSQL Runtime (ВМ №2)
# Production Blueprint v5.2
# ==============================================================================
# Назначение:
#   1. Создание full backup через pg_basebackup в SeaweedFS S3 (бакет postgres-backups).
#   2. Архивация WAL-файлов через pg_archivecleanup (ретентивность 30 дней).
#   3. Логирование результатов и отправка событий в Redis (опционально).
#
# Запуск:
#   - Вручную: sudo ./backups/backup.sh
#   - По cron: 0 2 * * * /opt/factory-vm2/backups/backup.sh >> /var/log/factory-backup.log 2>&1
#
# Требования:
#   - Запуск от пользователя postgres или root с доступом к контейнеру vm2-postgres-runtime
#   - Docker Compose запущен
#   - SeaweedFS S3 доступен
# ==============================================================================
set -euo pipefail

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"
LOG_FILE="/var/log/factory-vm2-backup.log"
BACKUP_RETENTION_DAYS=30

# Загрузка переменных из .env
if [[ -f "$ENV_FILE" ]]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

# Параметры подключения к PostgreSQL Runtime
PG_CONTAINER="vm2-postgres-runtime"
PG_USER="${POSTGRES_RUNTIME_USER:-factory_runtime}"
PG_DB="factory"
PG_BACKUP_USER="${POSTGRES_RUNTIME_USER:-factory_runtime}"

# Параметры S3 (SeaweedFS)
# Единые ключи для всех клиентов SeaweedFS (Langfuse, Worker, backups)
S3_ENDPOINT="${POSTGRES_BACKUP_S3_ENDPOINT:-${SEAWEED_S3_ENDPOINT:-https://seaweedfs-s3:8333}}"
S3_BUCKET="${SEAWEED_S3_BUCKET_BACKUPS:-postgres-backups}"
S3_ACCESS_KEY="${S3_ACCESS_KEY}"
S3_SECRET_KEY="${S3_SECRET_KEY}"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ==============================================================================
# ФУНКЦИИ
# ==============================================================================
log_info() {
    echo -e "${BLUE}[INFO]$(date '+%Y-%m-%d %H:%M:%S')${NC} $1" | tee -a "$LOG_FILE"
}

log_success() {
    echo -e "${GREEN}[OK]$(date '+%Y-%m-%d %H:%M:%S')${NC} $1" | tee -a "$LOG_FILE"
}

log_warn() {
    echo -e "${YELLOW}[WARN]$(date '+%Y-%m-%d %H:%M:%S')${NC} $1" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${RED}[ERROR]$(date '+%Y-%m-%d %H:%M:%S')${NC} $1" | tee -a "$LOG_FILE" >&2
}

require_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        log_error "Файл .env не найден: $ENV_FILE"
        exit 1
    fi
    if [[ -z "$S3_ACCESS_KEY" || -z "$S3_SECRET_KEY" ]]; then
        log_error "Переменные S3_ACCESS_KEY и S3_SECRET_KEY не установлены в .env"
        exit 1
    fi
}

require_docker() {
    if ! docker ps --format '{{.Names}}' | grep -q "^${PG_CONTAINER}$"; then
        log_error "Контейнер PostgreSQL не запущен: ${PG_CONTAINER}"
        log_error "Запустите: docker compose up -d"
        exit 1
    fi
    log_success "Контейнер PostgreSQL запущен: ${PG_CONTAINER}"
}

# ==============================================================================
# 1. FULL BACKUP (pg_basebackup)
# ==============================================================================
create_full_backup() {
    log_info "=== Шаг 1: Создание full backup через pg_basebackup ==="
    local backup_date
    backup_date=$(date +%Y%m%d_%H%M%S)
    local backup_dir="/tmp/pg_backup_${backup_date}"
    local s3_key="full/${backup_date}"

    log_info "Создание бэкапа в ${backup_dir}..."

    # Выполняем pg_basebackup внутри контейнера
    docker exec "$PG_CONTAINER" bash -c "
        pg_basebackup \
            -U ${PG_BACKUP_USER} \
            -D ${backup_dir} \
            -Ft \
            -z \
            -P \
            --checkpoint=fast \
            --wal-method=stream
    " 2>&1 | tee -a "$LOG_FILE"

    if [[ $? -ne 0 ]]; then
        log_error "pg_basebackup завершился с ошибкой"
        rm -rf "$backup_dir"
        return 1
    fi

    log_success "Бэкап создан: ${backup_dir}"

    # Загрузка в S3
    log_info "Загрузка бэкапа в S3: ${S3_BUCKET}/${s3_key}..."

    # Архивируем и загружаем
    tar -czf "/tmp/pg_backup_${backup_date}.tar.gz" -C /tmp "pg_backup_${backup_date}"

    # Используем curl для загрузки в S3 (SeaweedFS совместим с AWS S3 API)
    local file_size
    file_size=$(stat -c%s "/tmp/pg_backup_${backup_date}.tar.gz")

    curl --fail --silent --show-error --cacert "${S3_CA_CERT_PATH:-${PROJECT_DIR}/../certs/ca/corp-ca.crt}" \
        -X PUT "${S3_ENDPOINT}/${S3_BUCKET}/${s3_key}.tar.gz" \
        -H "Content-Type: application/gzip" \
        -H "Authorization: AWS ${S3_ACCESS_KEY}:${S3_SECRET_KEY}" \
        --data-binary "@/tmp/pg_backup_${backup_date}.tar.gz" \
        2>&1 | tee -a "$LOG_FILE"

    if [[ $? -eq 0 ]]; then
        log_success "Бэкап загружен в S3: ${s3_key}.tar.gz (${file_size} байт)"
    else
        log_error "Не удалось загрузить бэкап в S3"
        rm -f "/tmp/pg_backup_${backup_date}.tar.gz"
        return 1
    fi

    # Очистка временных файлов
    rm -rf "$backup_dir"
    rm -f "/tmp/pg_backup_${backup_date}.tar.gz"

    log_success "Full backup завершён"
}

# ==============================================================================
# 2. WAL ARCHIVE CLEANUP
# ==============================================================================
cleanup_wal_archive() {
    log_info "=== Шаг 2: Очистка старых WAL-файлов (ретентивность ${BACKUP_RETENTION_DAYS} дней) ==="
    local wal_archive_dir="/var/lib/postgresql/wal-archive"

    # Проверяем, смонтирован ли том WAL-архива
    if ! docker exec "$PG_CONTAINER" test -d "$wal_archive_dir"; then
        log_warn "Директория WAL-архива не найдена: ${wal_archive_dir}"
        log_warn "Убедитесь, что том backups-wal-archive смонтирован в docker-compose.yml"
        return 0
    fi

    # Удаляем файлы старше BACKUP_RETENTION_DAYS дней
    local deleted_count
    deleted_count=$(docker exec "$PG_CONTAINER" bash -c "
        find ${wal_archive_dir} -type f -mtime +${BACKUP_RETENTION_DAYS} -delete -print | wc -l
    " 2>&1)

    if [[ $? -eq 0 ]]; then
        log_success "Удалено старых WAL-файлов: ${deleted_count}"
    else
        log_warn "Не удалось выполнить очистку WAL-архива"
    fi
}

# ==============================================================================
# 3. ПРОВЕРКА ЦЕЛОСТНОСТИ ПОСЛЕДНЕГО БЭКАПА
# ==============================================================================
verify_last_backup() {
    log_info "=== Шаг 3: Проверка последнего бэкапа ==="

    # Получаем список последних бэкапов из S3
    local last_backup
    last_backup=$(curl -s "${S3_ENDPOINT}/${S3_BUCKET}/full/" \
        -H "Authorization: AWS ${S3_ACCESS_KEY}:${S3_SECRET_KEY}" \
        2>/dev/null | grep -oP '(?<=<Key>full/)[^<]+' | sort -r | head -1)

    if [[ -z "$last_backup" ]]; then
        log_warn "Бэкапы не найдены в S3: ${S3_BUCKET}/full/"
        return 0
    fi

    log_success "Последний бэкап: ${last_backup}"

    # Проверка размера (должен быть > 1 MB)
    local backup_size
    backup_size=$(curl -s -I "${S3_ENDPOINT}/${S3_BUCKET}/full/${last_backup}" \
        -H "Authorization: AWS ${S3_ACCESS_KEY}:${S3_SECRET_KEY}" \
        2>/dev/null | grep -i "Content-Length" | awk '{print $2}' | tr -d '\r')

    if [[ -n "$backup_size" && "$backup_size" -gt 1048576 ]]; then
        log_success "Размер бэкапа: ${backup_size} байт (корректный)"
    else
        log_warn "Размер бэкапа подозрительно мал: ${backup_size} байт"
    fi
}

# ==============================================================================
# 4. УВЕДОМЛЕНИЕ (опционально)
# ==============================================================================
send_notification() {
    local status=$1
    local message=$2

    # TODO: Интеграция с Telegram/Email/Slack для уведомлений о бэкапах
    # Пример:
    # curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    #     -d "chat_id=${TELEGRAM_CHAT_ID}&text=Backup ${status}: ${message}"

    log_info "Уведомление: Backup ${status} - ${message}"
}

# ==============================================================================
# ОСНОВНАЯ ЛОГИКА
# ==============================================================================
main() {
    log_info "=============================================="
    log_info "Запуск бэкапа PostgreSQL Runtime"
    log_info "=============================================="
    log_info ""

    require_env
    require_docker

    echo ""

    # Создание full backup
    if create_full_backup; then
        send_notification "success" "Full backup создан и загружен в S3"
    else
        send_notification "error" "Не удалось создать full backup"
        exit 1
    fi

    echo ""

    # Очистка WAL-архива
    cleanup_wal_archive

    echo ""

    # Проверка целостности
    verify_last_backup

    echo ""

    log_info "=============================================="
    log_success "Бэкап завершён успешно"
    log_info "=============================================="
}

main "$@"