#!/usr/bin/env bash
# ==============================================================================
# render-configs.sh — Рендеринг конфигурационных файлов ВМ №1
# Production Blueprint v5.2
# ==============================================================================
# Назначение:
#   Рендеринг шаблонов конфигов, которые не поддерживают переменные окружения
#   нативно. На ВМ №1 это файл репрезентации Keycloak (realm-export.json).
#
# Что делает:
#   1. Подставляет переменные окружения из .env в шаблон через envsubst.
#   2. Вычищает служебные ключи с префиксом "_" из репрезентации
#      (_ldap_template, _comment_* и т.д.), которые находятся вне схемы
#      и могут вызвать отказ импорта репрезентации.
#   3. Сохраняет результат в ./rendered/.
#
# Использование:
#   ./render-configs.sh
#
# Требования:
#   - .env заполнен (скопирован из .env.example)
#   - envsubst установлен (пакет gettext)
#   - python3 или jq установлен (для очистки _*-ключей из JSON)
#
# Результат:
#   ./rendered/realm-export.json — монтируется в docker-compose в
#   /opt/keycloak/data/import/realm-export.json (--import-realm).
# ==============================================================================

set -euo pipefail

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
KEYCLOAK_DIR="${SCRIPT_DIR}/keycloak"
RENDERED_DIR="${SCRIPT_DIR}/rendered"

# Шаблон репрезентации и целевой отрендеренный файл
REALM_TEMPLATE="${KEYCLOAK_DIR}/realm-export.json"
REALM_RENDERED="${RENDERED_DIR}/realm-export.json"

# Переменные, которые разрешено подставлять через envsubst.
# Явный список защищает от случайного раскрытия прочих переменных окружения
# и сохраняет валидность JSON (подстановка идёт только для указанных имён).
ENVSUBST_VARS='${OAUTH2_PROXY_CLIENT_SECRET}'

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ==============================================================================
# ЛОГИРОВАНИЕ
# ==============================================================================
log_info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# ==============================================================================
# ПРОВЕРКИ
# ==============================================================================

# Проверка наличия .env
require_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        log_error "Файл .env не найден: ${ENV_FILE}"
        log_error "Скопируйте .env.example в .env и заполните значения:"
        log_error "  cp .env.example .env && nano .env"
        exit 1
    fi
    log_info "Файл .env найден: ${ENV_FILE}"
}

# Проверка наличия envsubst
require_envsubst() {
    if ! command -v envsubst &>/dev/null; then
        log_error "envsubst не найден."
        log_error "Установите пакет gettext:"
        log_error "  Debian/Ubuntu: sudo apt-get install gettext-base"
        log_error "  RHEL/CentOS:   sudo yum install gettext"
        exit 1
    fi
    log_info "envsubst найден: $(command -v envsubst)"
}

# Определение инструмента для очистки _*-ключей из JSON.
# Предпочтение: python3 → jq. Возвращает имя инструмента или завершает работу.
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

# Проверка наличия шаблона репрезентации
require_realm_template() {
    if [[ ! -f "$REALM_TEMPLATE" ]]; then
        log_error "Шаблон репрезентации не найден: ${REALM_TEMPLATE}"
        exit 1
    fi
    log_info "Шаблон репрезентации найден: ${REALM_TEMPLATE}"
}

# Проверка наличия обязательных переменных в .env
require_vars() {
    # Загружаем .env в окружение для проверок и подстановки
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a

    if [[ -z "${OAUTH2_PROXY_CLIENT_SECRET:-}" ]]; then
        log_error "Переменная OAUTH2_PROXY_CLIENT_SECRET не задана в .env"
        exit 1
    fi

    if [[ "${OAUTH2_PROXY_CLIENT_SECRET}" == *CHANGE_ME* ]]; then
        log_warn "OAUTH2_PROXY_CLIENT_SECRET содержит плейсхолдер 'CHANGE_ME'."
        log_warn "Получите реальный секрет клиента в Keycloak и обновите .env."
    fi
}

# ==============================================================================
# РЕНДЕРИНГ РЕПРЕЗЕНТАЦИИ
# ==============================================================================
render_realm() {
    local json_tool
    json_tool="$(detect_json_tool)"
    log_info "Инструмент для очистки JSON: ${json_tool}"

    mkdir -p "$RENDERED_DIR"
    log_info "Директория вывода: ${RENDERED_DIR}"

    # --------------------------------------------------------------------------
    # Шаг 1. Подстановка переменных окружения.
    # Загружаем .env в окружение (если ещё не загружено) и подставляем только
    # переменные из явного списка — это защищает прочие переменные окружения.
    # --------------------------------------------------------------------------
    log_info "Подстановка переменных окружения (envsubst)..."
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a

    local substituted
    substituted="$(envsubst "${ENVSUBST_VARS}" < "$REALM_TEMPLATE")"

    # --------------------------------------------------------------------------
    # Шаг 2. Очистка служебных ключей с префиксом "_" из репрезентации.
    # Удаляются на всех уровнях вложенности (например, _ldap_template,
    # _comment_ldap_federation), чтобы импорт в Keycloak не падал на
    # неизвестных полях.
    # --------------------------------------------------------------------------
    log_info "Очистка служебных _*-ключей (${json_tool})..."
    if [[ "$json_tool" == "python3" ]]; then
        printf '%s' "$substituted" | python3 -c '
import json, sys

def clean(obj):
    if isinstance(obj, dict):
        return {
            k: clean(v)
            for k, v in obj.items()
            if not k.startswith("_")
        }
    if isinstance(obj, list):
        return [clean(item) for item in obj]
    return obj

data = json.load(sys.stdin)
print(json.dumps(clean(data), ensure_ascii=False, indent=2))
' > "$REALM_RENDERED"
    else
        # jq: рекурсивно удаляем все ключи, начинающиеся с "_".
        # Функция walk доступна в jq 1.6+.
        printf '%s' "$substituted" | jq 'walk(
            if type == "object"
            then with_entries(select(.key | startswith("_") | not))
            else .
            end
        )' > "$REALM_RENDERED"
    fi

    # --------------------------------------------------------------------------
    # Шаг 3. Валидация результата: результат должен быть валидным JSON.
    # --------------------------------------------------------------------------
    log_info "Валидация результата..."
    if [[ "$json_tool" == "python3" ]]; then
        python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$REALM_RENDERED"
    else
        jq empty "$REALM_RENDERED"
    fi

    log_info "Отрендерен: ${REALM_TEMPLATE} -> ${REALM_RENDERED}"
}

# ==============================================================================
# ОСНОВНАЯ ЛОГИКА
# ==============================================================================
main() {
    echo "=============================================="
    echo "Рендеринг конфигурационных файлов ВМ №1"
    echo "=============================================="
    echo ""

    require_env
    require_envsubst
    require_realm_template
    require_vars

    echo ""
    render_realm

    echo ""
    echo "=============================================="
    echo "Рендеринг завершён успешно"
    echo "=============================================="
    echo ""
    echo "Дальнейшие шаги:"
    echo "  1. Убедитесь, что ./rendered/ добавлен в исключения системы"
    echo "     контроля версий (содержит секреты)."
    echo "  2. Запустите стек: docker compose up -d"
    echo ""
    echo "См. также: ${SCRIPT_DIR}/README.md"
}

main "$@"