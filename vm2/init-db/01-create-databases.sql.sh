#!/bin/bash
# vm2/init-db/01-create-databases.sql.sh
# ==============================================================================
# 01-create-databases.sql.sh — ВМ №2: инициализация БД factory, пользователей, базовых таблиц
# Production Blueprint v5.2
# ==============================================================================
# Выполняется автоматически при ПЕРВОМ запуске контейнера postgres-runtime.
# Формат .sql.sh гарантирует, что официальный образ исполнит скрипт как bash
# с доступом к переменным окружения из docker-compose.
#
# По Blueprint §3.1: ВМ №2 обслуживает чекпоинты LangGraph, реестр ранов
# фабрики и векторный поиск pgvector.
#
# Переменные подставляются из .env → docker-compose → environment контейнера:
#   FACTORY_DB_USER, FACTORY_DB_PASSWORD
#   WORKER_DB_USER, WORKER_DB_PASSWORD
#   GATEWAY_DB_USER, GATEWAY_DB_PASSWORD
# ==============================================================================
set -euo pipefail

echo "=== Создание реестра ранов фабрики (ВМ №2) ==="

# --------------------------------------------------------------------------
# 1. База factory (реестр ранов, чекпоинты)
# --------------------------------------------------------------------------
echo "Создание БД: factory, владелец: ${FACTORY_DB_USER}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE factory;
    CREATE USER ${FACTORY_DB_USER} WITH ENCRYPTED PASSWORD '${FACTORY_DB_PASSWORD}';
    GRANT ALL PRIVILEGES ON DATABASE factory TO ${FACTORY_DB_USER};
    ALTER DATABASE factory OWNER TO ${FACTORY_DB_USER};
EOSQL

# Настройка схемы внутри базы
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "factory" <<-EOSQL
    GRANT ALL ON SCHEMA public TO ${FACTORY_DB_USER};
EOSQL

echo "База factory готова."

# --------------------------------------------------------------------------
# 2. Пользователь Worker (для LangGraph, чекпоинты, все таблицы)
# --------------------------------------------------------------------------
echo "Создание пользователя Worker: ${WORKER_DB_USER}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "factory" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${WORKER_DB_USER}') THEN
            CREATE ROLE ${WORKER_DB_USER} WITH LOGIN ENCRYPTED PASSWORD '${WORKER_DB_PASSWORD}';
        END IF;
    END
    \$\$;
    GRANT CONNECT ON DATABASE factory TO ${WORKER_DB_USER};
    GRANT USAGE ON SCHEMA public TO ${WORKER_DB_USER};
    -- D2: PostgresSaver.setup() создаёт таблицы чекпоинтов под ролью Worker
    GRANT CREATE ON SCHEMA public TO ${WORKER_DB_USER};
    GRANT ALL ON ALL TABLES IN SCHEMA public TO ${WORKER_DB_USER};
    GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO ${WORKER_DB_USER};
EOSQL

echo "Пользователь ${WORKER_DB_USER} готов."

# --------------------------------------------------------------------------
# 3. Пользователь Gateway (только таблица runs, read/write)
# --------------------------------------------------------------------------
echo "Создание пользователя Gateway: ${GATEWAY_DB_USER}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "factory" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${GATEWAY_DB_USER}') THEN
            CREATE ROLE ${GATEWAY_DB_USER} WITH LOGIN ENCRYPTED PASSWORD '${GATEWAY_DB_PASSWORD}';
        END IF;
    END
    \$\$;
    GRANT CONNECT ON DATABASE factory TO ${GATEWAY_DB_USER};
    GRANT USAGE ON SCHEMA public TO ${GATEWAY_DB_USER};
EOSQL

echo "Пользователь ${GATEWAY_DB_USER} готов."

# --------------------------------------------------------------------------
# 4. Таблица runs (реестр ранов) — Blueprint §3.1, §5.6
# --------------------------------------------------------------------------
echo "Создание таблицы runs..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "factory" <<-EOSQL
    CREATE TABLE IF NOT EXISTS runs (
        run_id UUID PRIMARY KEY,
        thread_id UUID NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'QUEUED' CHECK (status IN (
            'QUEUED', 'RUNNING', 'WAITING_HUMAN', 'SUCCEEDED', 'FAILED', 'CANCELLED'
        )),
        "user" VARCHAR(255) NOT NULL,
        interrupt_id UUID,
        graph_version VARCHAR(64),
        payload JSONB,
        error TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        started_at TIMESTAMP WITH TIME ZONE,
        finished_at TIMESTAMP WITH TIME ZONE
    );

    -- Индексы для быстрого поиска (Blueprint §5.6: реконнект, идемпотентность)
    CREATE INDEX IF NOT EXISTS idx_runs_thread_id ON runs(thread_id);
    CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
    CREATE INDEX IF NOT EXISTS idx_runs_user ON runs("user");
    CREATE INDEX IF NOT EXISTS idx_runs_interrupt_id ON runs(interrupt_id)
        WHERE interrupt_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_runs_thread_created ON runs(thread_id, created_at DESC);

    -- Привилегии для Gateway (read/write по таблице runs)
    GRANT SELECT, INSERT, UPDATE ON runs TO ${GATEWAY_DB_USER};
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ${GATEWAY_DB_USER};

    -- Привилегии для Worker (полный доступ ко всем таблицам)
    GRANT ALL ON runs TO ${WORKER_DB_USER};

    -- Привилегии для владельца БД (полный доступ)
    GRANT ALL ON runs TO ${FACTORY_DB_USER};
EOSQL

echo "Таблица runs готова."

# --------------------------------------------------------------------------
# 5. Таблица thread_locks (блокировки тредов)
# --------------------------------------------------------------------------
echo "Создание таблицы thread_locks..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "factory" <<-EOSQL
    CREATE TABLE IF NOT EXISTS thread_locks (
        thread_id UUID PRIMARY KEY,
        run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        locked_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        locked_by VARCHAR(255) NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_thread_locks_run_id ON thread_locks(run_id);

    GRANT ALL ON thread_locks TO ${WORKER_DB_USER};
    GRANT ALL ON thread_locks TO ${FACTORY_DB_USER};
    -- C2: Gateway управляет lease (INSERT/DELETE) и читает статус блокировки
    GRANT SELECT, INSERT, DELETE ON thread_locks TO ${GATEWAY_DB_USER};
EOSQL

echo "Таблица thread_locks готова."

# --------------------------------------------------------------------------
# 6. Таблица interrupt_decisions (решения HITL)
# --------------------------------------------------------------------------
echo "Создание таблицы interrupt_decisions..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "factory" <<-EOSQL
    CREATE TABLE IF NOT EXISTS interrupt_decisions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        interrupt_id UUID NOT NULL,
        "user" VARCHAR(255) NOT NULL,
        decision JSONB NOT NULL,
        decided_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        UNIQUE(run_id, interrupt_id)
    );

    CREATE INDEX IF NOT EXISTS idx_interrupt_decisions_run_id ON interrupt_decisions(run_id);
    CREATE INDEX IF NOT EXISTS idx_interrupt_decisions_user ON interrupt_decisions("user");

    GRANT ALL ON interrupt_decisions TO ${WORKER_DB_USER};
    GRANT ALL ON interrupt_decisions TO ${FACTORY_DB_USER};
    -- C2: Gateway фиксирует HITL-решение (дедуп по UNIQUE(run_id, interrupt_id)); Worker читает при resume
    GRANT SELECT, INSERT, DELETE ON interrupt_decisions TO ${GATEWAY_DB_USER};
EOSQL

echo "Таблица interrupt_decisions готова."

# --------------------------------------------------------------------------
# 7. Таблица documents (документы и артефакты)
# --------------------------------------------------------------------------
echo "Создание таблицы documents..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "factory" <<-EOSQL
    CREATE TABLE IF NOT EXISTS documents (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        run_id UUID REFERENCES runs(run_id) ON DELETE SET NULL,
        document_type VARCHAR(64) NOT NULL CHECK (document_type IN (
            'draft', 'final_act', 'contract', 'supplementary_agreement', 'other'
        )),
        contract_id VARCHAR(255),
        file_path VARCHAR(512) NOT NULL,
        file_size BIGINT,
        file_hash VARCHAR(128),
        uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        metadata JSONB DEFAULT '{}'::jsonb
    );

    CREATE INDEX IF NOT EXISTS idx_documents_run_id ON documents(run_id);
    CREATE INDEX IF NOT EXISTS idx_documents_contract_id ON documents(contract_id);
    CREATE INDEX IF NOT EXISTS idx_documents_document_type ON documents(document_type);

    GRANT ALL ON documents TO ${WORKER_DB_USER};
    GRANT ALL ON documents TO ${FACTORY_DB_USER};
EOSQL

echo "Таблица documents готова."

# --------------------------------------------------------------------------
# 8. Таблица rates (Единый корпоративный реестр ставок — ЕРР)
# --------------------------------------------------------------------------
echo "Создание таблицы rates..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "factory" <<-EOSQL
    CREATE TABLE IF NOT EXISTS rates (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        contract_id VARCHAR(255) NOT NULL,
        item_name VARCHAR(512) NOT NULL,
        unit VARCHAR(64) NOT NULL,
        price NUMERIC(15, 2) NOT NULL CHECK (price >= 0),
        valid_from DATE NOT NULL,
        valid_to DATE,
        source_document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
        source_page INTEGER,
        verified_by VARCHAR(255),
        verified_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_rates_contract_id ON rates(contract_id);
    CREATE INDEX IF NOT EXISTS idx_rates_item_name ON rates(item_name);
    CREATE INDEX IF NOT EXISTS idx_rates_valid_from ON rates(valid_from);
    CREATE INDEX IF NOT EXISTS idx_rates_valid_to ON rates(valid_to);

    GRANT ALL ON rates TO ${WORKER_DB_USER};
    GRANT ALL ON rates TO ${FACTORY_DB_USER};
EOSQL

echo "Таблица rates готова."

# --------------------------------------------------------------------------
# 9. Таблица verifications (результаты сверки)
# --------------------------------------------------------------------------
echo "Создание таблицы verifications..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "factory" <<-EOSQL
    CREATE TABLE IF NOT EXISTS verifications (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        rate_id UUID REFERENCES rates(id) ON DELETE SET NULL,
        status VARCHAR(32) NOT NULL CHECK (status IN (
            'matched', 'mismatched', 'missing_rate', 'low_confidence'
        )),
        expected_value NUMERIC(15, 2),
        actual_value NUMERIC(15, 2),
        difference NUMERIC(15, 2),
        confidence NUMERIC(5, 4) CHECK (confidence >= 0 AND confidence <= 1),
        page_number INTEGER,
        cell_reference VARCHAR(64),
        evidence JSONB DEFAULT '{}'::jsonb,
        verified_by VARCHAR(255),
        verified_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_verifications_run_id ON verifications(run_id);
    CREATE INDEX IF NOT EXISTS idx_verifications_document_id ON verifications(document_id);
    CREATE INDEX IF NOT EXISTS idx_verifications_rate_id ON verifications(rate_id);
    CREATE INDEX IF NOT EXISTS idx_verifications_status ON verifications(status);

    GRANT ALL ON verifications TO ${WORKER_DB_USER};
    GRANT ALL ON verifications TO ${FACTORY_DB_USER};
EOSQL

echo "Таблица verifications готова."

# --------------------------------------------------------------------------
# 10. Триггер автоматического обновления updated_at
# --------------------------------------------------------------------------
echo "Создание триггеров updated_at..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "factory" <<-EOSQL
    CREATE OR REPLACE FUNCTION update_updated_at_column()
    RETURNS TRIGGER AS \$\$
    BEGIN
        NEW.updated_at = NOW();
        RETURN NEW;
    END;
    \$\$ language 'plpgsql';

    DROP TRIGGER IF EXISTS trigger_runs_updated_at ON runs;
    CREATE TRIGGER trigger_runs_updated_at
        BEFORE UPDATE ON runs
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();

    DROP TRIGGER IF EXISTS trigger_rates_updated_at ON rates;
    CREATE TRIGGER trigger_rates_updated_at
        BEFORE UPDATE ON rates
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
EOSQL

echo "Триггеры готовы."

# --------------------------------------------------------------------------
# 11. Итоговая проверка
# --------------------------------------------------------------------------
echo "Проверка созданных объектов..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "factory" <<-EOSQL
    \dt
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'runs'
    ORDER BY ordinal_position;
EOSQL

echo "=== Инициализация реестра ранов ВМ №2 завершена ==="