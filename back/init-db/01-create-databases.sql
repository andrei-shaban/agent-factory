#!/bin/bash
# ==============================================================================
# 01-create-databases.sql — ВМ №1: инициализация баз данных
# Production Blueprint v5.2
# ==============================================================================
# Выполняется автоматически при ПЕРВОМ запуске контейнера postgres.
# Формат .sql.sh гарантирует, что официальный образ исполнит скрипт как bash
# с доступом к переменным окружения из docker-compose.
#
# По Blueprint §2, §3.1: ВМ №1 обслуживает ТОЛЬКО базы Keycloak и Langfuse.
# База "factory" (реестр ранов) живёт на ВМ №2 — здесь НЕ создаётся.
#
# Переменные подставляются из .env → docker-compose → environment контейнера:
#   KEYCLOAK_DB_USER, KEYCLOAK_DB_PASSWORD
#   LANGFUSE_DB_USER, LANGFUSE_DB_PASSWORD
# ==============================================================================

set -euo pipefail

echo "=== Создание баз данных ВМ №1 (Ключей и наблюдение) ==="

# --------------------------------------------------------------------------
# 1. База Keycloak
# --------------------------------------------------------------------------
echo "Создание БД: keycloak, пользователь: ${KEYCLOAK_DB_USER}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE keycloak;
    CREATE USER ${KEYCLOAK_DB_USER} WITH ENCRYPTED PASSWORD '${KEYCLOAK_DB_PASSWORD}';
    GRANT ALL PRIVILEGES ON DATABASE keycloak TO ${KEYCLOAK_DB_USER};
    ALTER DATABASE keycloak OWNER TO ${KEYCLOAK_DB_USER};
EOSQL

# Настройка схемы внутри базы (нужно подключиться к самой базе)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "keycloak" <<-EOSQL
    GRANT ALL ON SCHEMA public TO ${KEYCLOAK_DB_USER};
EOSQL

echo "База keycloak готова."

# --------------------------------------------------------------------------
# 2. База Langfuse
# --------------------------------------------------------------------------
echo "Создание БД: langfuse, пользователь: ${LANGFUSE_DB_USER}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE langfuse;
    CREATE USER ${LANGFUSE_DB_USER} WITH ENCRYPTED PASSWORD '${LANGFUSE_DB_PASSWORD}';
    GRANT ALL PRIVILEGES ON DATABASE langfuse TO ${LANGFUSE_DB_USER};
    ALTER DATABASE langfuse OWNER TO ${LANGFUSE_DB_USER};
EOSQL

# Настройка схемы внутри базы
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "langfuse" <<-EOSQL
    GRANT ALL ON SCHEMA public TO ${LANGFUSE_DB_USER};
EOSQL

echo "База langfuse готова."

# --------------------------------------------------------------------------
# 3. Итоговая проверка
# --------------------------------------------------------------------------
echo "Проверка созданных баз..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;
EOSQL

echo "=== Инициализация баз ВМ №1 завершена ==="