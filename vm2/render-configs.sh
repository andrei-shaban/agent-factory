# vm2/render-configs.sh
#!/usr/bin/env bash
# ==============================================================================
# render-configs.sh — Рендеринг конфигурационных файлов для ВМ №2
# Production Blueprint v5.2
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
#            docker-compose.yml монтирует файлы из rendered/, а не оригиналы.
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
# Единые S3-ключи для всех клиентов SeaweedFS (Langfuse, Worker, backups)
REQUIRED_VARS=(
    "REDIS_FACTORY_PASSWORD"
    "REDIS_FACTORY_ACL_PASSWORD"
    "S3_ACCESS_KEY"
    "S3_SECRET_KEY"
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
    # Source a normalized temporary copy so Windows CRLF lines do not execute stray CR tokens.
    local normalized_env
    normalized_env="$(mktemp)"
    tr -d '\r' < "$ENV_FILE" > "$normalized_env"
    set -a
    source "$normalized_env"
    set +a
    rm -f "$normalized_env"
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

# Определение инструмента для очистки _*-ключей из JSON
detect_json_tool() {
    if command -v python3 &>/dev/null; then
        echo "python3"
    elif command -v jq &>/dev/null; then
        echo "jq"
    else
        log_error "Не найден инструмент для обработки JSON (python3 или jq)."
        log_error "Установите один из них:"
        log_error "  python3: sudo apt-get install python3"
        log_error "  jq:      sudo apt-get install jq"
        exit 1
    fi
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

# Очистка служебных _*-ключей из JSON-файлов
clean_json_metadata() {
    local json_file="$1"
    local json_tool
    json_tool="$(detect_json_tool)"

    if [[ ! -f "$json_file" ]]; then
        log_error "JSON-файл не найден: $json_file"
        return 1
    fi

    # Проверяем, является ли файл JSON
    if [[ "$json_file" != *.json ]]; then
        return 0
    fi

    log_info "Очистка служебных _*-ключей из $(basename "$json_file") (${json_tool})..."

    if [[ "$json_tool" == "python3" ]]; then
        python3 -c "
import json, sys
def clean(obj):
    if isinstance(obj, dict):
        return {
            k: clean(v)
            for k, v in obj.items()
            if not k.startswith('_')
        }
    if isinstance(obj, list):
        return [clean(item) for item in obj]
    return obj
data = json.load(open(sys.argv[1]))
print(json.dumps(clean(data), ensure_ascii=False, indent=2))
" "$json_file" > "${json_file}.tmp" && mv "${json_file}.tmp" "$json_file"
    else
        # jq: рекурсивно удаляем все ключи, начинающиеся с "_"
        jq 'walk(
            if type == "object"
            then with_entries(select(.key | startswith("_") | not))
            else .
            end
        )' "$json_file" > "${json_file}.tmp" && mv "${json_file}.tmp" "$json_file"
    fi

    # Валидация результата
    if [[ "$json_tool" == "python3" ]]; then
        python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$json_file"
    else
        jq empty "$json_file"
    fi

    log_success "JSON очищен: $(basename "$json_file")"
}

# Очистка старых rendered файлов
clean_rendered() {
    if [[ -d "$RENDERED_DIR" ]]; then
        log_info "Очистка старой директории rendered/"
        rm -rf "$RENDERED_DIR"
    fi
    mkdir -p "$RENDERED_DIR"
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
            ((++success_count))
        else
            ((++fail_count))
        fi
    done

    # Очистка _*-ключей из JSON-файлов
    log_info ""
    log_info "Очистка служебных _*-ключей из JSON..."
    for file in "${FILES_TO_RENDER[@]}"; do
        if [[ "$file" == *.json ]]; then
            clean_json_metadata "${RENDERED_DIR}/${file}"
        fi
    done
    # SeaweedFS S3 IAM config accepts identities only; discard blueprint metadata and local bucket annotations.
    python3 - "${RENDERED_DIR}/seaweedfs/s3.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    config = json.load(f)
config = {k: v for k, v in config.items() if k in {"identities", "actions"}}
with open(path, "w", encoding="utf-8") as f:
    json.dump(config, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

    log_info ""
    log_info "================================================================"
    log_info "Результат: $success_count успешно, $fail_count ошибок"
    log_info "================================================================"
    if [[ $fail_count -gt 0 ]]; then
        log_error "Некоторые файлы не удалось отрендерить"
        exit 1
    fi

    log_success "Готово! Теперь можно запускать: docker compose up -d"
}

# Запуск
main "$@"