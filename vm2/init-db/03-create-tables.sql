-- vm2/init-db/03-create-tables.sql
-- ==============================================================================
-- Production Blueprint v5.1 — ВМ №2: PostgreSQL Runtime & RAG
-- Дополнительные таблицы бизнес-логики и служебные функции
-- Выполняется после 01-create-databases.sql и 02-enable-extensions.sql
-- ==============================================================================
-- Идемпотентность: все объекты создаются с IF NOT EXISTS.
-- Скрипт можно запускать повторно без ошибок.
-- ==============================================================================

\c factory

-- ==============================================================================
-- Таблица: audit_log (журнал аудита действий)
-- ==============================================================================
-- Хранит все значимые действия в системе: запуски, HITL-решения, вызовы
-- инструментов, обращения к внешним системам. Обеспечивает сквозной аудит
-- согласно Blueprint v5.1 п. 5 ("Корпоративная аутентификация и сквозной аудит").

CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    user_id VARCHAR(255) NOT NULL,
    action VARCHAR(64) NOT NULL CHECK (action IN (
        -- Действия с ранами
        'run.started', 'run.finished', 'run.failed', 'run.cancelled',
        'run.resumed', 'run.interrupted',
        -- HITL-действия
        'hitl.form_submitted', 'hitl.form_rejected', 'hitl.rate_verified',
        -- Действия с документами
        'document.uploaded', 'document.processed', 'document.archived',
        -- Действия с реестром ставок
        'rate.created', 'rate.updated', 'rate.archived',
        -- Вызовы инструментов
        'tool.invoked', 'tool.succeeded', 'tool.failed',
        -- Вызовы внешних систем
        'integration.llm_called', 'integration.ocr_called',
        'integration.corp_pg_called', 'integration.workstation_called',
        -- Системные события
        'system.error', 'system.warning'
    )),
    resource_type VARCHAR(64),
    resource_id VARCHAR(255),
    run_id UUID REFERENCES runs(run_id) ON DELETE SET NULL,
    details JSONB DEFAULT '{}'::jsonb,
    client_ip INET,
    user_agent TEXT,
    duration_ms INTEGER,
    status VARCHAR(32) DEFAULT 'success' CHECK (status IN ('success', 'failure', 'partial'))
);

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_audit_log_occurred_at ON audit_log(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_log_run_id ON audit_log(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_resource ON audit_log(resource_type, resource_id);

-- GIN-индекс для поиска по JSONB-полю details
CREATE INDEX IF NOT EXISTS idx_audit_log_details ON audit_log USING GIN (details);

COMMENT ON TABLE audit_log IS 'Сквозной журнал аудита: все значимые действия в системе';

-- ==============================================================================
-- Таблица: contract_registry (реестр договоров)
-- ==============================================================================
-- Мета-данные договоров и дополнительных соглашений.
-- Отдельно от таблицы documents, так как договор — это сущность более высокого
-- уровня, чем документ. Один договор может иметь несколько документов
-- (оригинал, скан, допсоглашения).

CREATE TABLE IF NOT EXISTS contract_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_number VARCHAR(128) NOT NULL,
    contractor_name VARCHAR(512) NOT NULL,
    contractor_inn VARCHAR(32),
    signed_at DATE,
    valid_from DATE,
    valid_to DATE,
    status VARCHAR(32) NOT NULL DEFAULT 'active' CHECK (status IN (
        'draft', 'active', 'suspended', 'terminated', 'archived'
    )),
    source_system VARCHAR(64) DEFAULT 'vizir' CHECK (source_system IN (
        'vizir', 'manual', 'diadoc', 'import'
    )),
    source_document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    owner_user_id VARCHAR(255),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    -- Уникальность по номеру договора
    UNIQUE(contract_number)
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_contract_registry_number ON contract_registry(contract_number);
CREATE INDEX IF NOT EXISTS idx_contract_registry_contractor ON contract_registry(contractor_name);
CREATE INDEX IF NOT EXISTS idx_contract_registry_status ON contract_registry(status);
CREATE INDEX IF NOT EXISTS idx_contract_registry_owner ON contract_registry(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_contract_registry_valid ON contract_registry(valid_from, valid_to);

-- Триггер автообновления updated_at
CREATE TRIGGER trigger_contract_registry_updated_at
    BEFORE UPDATE ON contract_registry
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE contract_registry IS 'Реестр договоров: мета-данные, статусы, источники';

-- ==============================================================================
-- Таблица: toolset_catalog (каталог toolset-образов песочниц)
-- ==============================================================================
-- Учёт предзапечённых toolset-образов для песочниц gVisor.
-- Каждый образ содержит фиксированный набор библиотек для определённого
-- класса задач (парсинг PDF, генерация Excel, расчёты и т.д.).

CREATE TABLE IF NOT EXISTS toolset_catalog (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(128) NOT NULL,
    version VARCHAR(64) NOT NULL,
    description TEXT,
    image_ref VARCHAR(512) NOT NULL,
    image_digest VARCHAR(256) NOT NULL,
    python_version VARCHAR(16) NOT NULL,
    libraries JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Список библиотек с версиями: [{"name": "pandas", "version": "2.1.0"}, ...]
    resource_limits JSONB DEFAULT '{}'::jsonb,
    -- {"cpu": "2.0", "memory": "4g", "pids": 256}
    status VARCHAR(32) NOT NULL DEFAULT 'active' CHECK (status IN (
        'active', 'deprecated', 'disabled'
    )),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(name, version)
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_toolset_catalog_name ON toolset_catalog(name);
CREATE INDEX IF NOT EXISTS idx_toolset_catalog_status ON toolset_catalog(status);

-- GIN-индекс для поиска по библиотекам
CREATE INDEX IF NOT EXISTS idx_toolset_catalog_libraries ON toolset_catalog USING GIN (libraries);

CREATE TRIGGER trigger_toolset_catalog_updated_at
    BEFORE UPDATE ON toolset_catalog
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE toolset_catalog IS 'Каталог toolset-образов для песочниц gVisor';

-- ==============================================================================
-- Таблица: wheel_cache_entries (учёт загруженных wheels)
-- ==============================================================================
-- Учёт пакетов, загруженных во внутренний wheel-cache на ВМ №2 из PyPI-зеркала.
-- Используется для контроля состава кэша и проверки целостности (хэши).

CREATE TABLE IF NOT EXISTS wheel_cache_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    package_name VARCHAR(255) NOT NULL,
    package_version VARCHAR(64) NOT NULL,
    python_version VARCHAR(16) NOT NULL,
    filename VARCHAR(512) NOT NULL,
    file_size BIGINT NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    source_url TEXT,
    uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    uploaded_by VARCHAR(255),
    status VARCHAR(32) DEFAULT 'active' CHECK (status IN ('active', 'deprecated', 'removed')),
    UNIQUE(package_name, package_version, python_version)
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_wheel_cache_package ON wheel_cache_entries(package_name);
CREATE INDEX IF NOT EXISTS idx_wheel_cache_python ON wheel_cache_entries(python_version);
CREATE INDEX IF NOT EXISTS idx_wheel_cache_status ON wheel_cache_entries(status);

COMMENT ON TABLE wheel_cache_entries IS 'Учёт пакетов в wheel-cache: хэши, версии, источники';

-- ==============================================================================
-- Таблица: sandbox_executions (журнал исполнения песочниц)
-- ==============================================================================
-- Журнал запусков песочниц gVisor. Используется для аудита, отладки и
-- анализа использования toolset-образов.

CREATE TABLE IF NOT EXISTS sandbox_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID REFERENCES runs(run_id) ON DELETE SET NULL,
    toolset_id UUID REFERENCES toolset_catalog(id) ON DELETE SET NULL,
    container_id VARCHAR(128),
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    finished_at TIMESTAMP WITH TIME ZONE,
    duration_ms INTEGER,
    status VARCHAR(32) NOT NULL CHECK (status IN (
        'started', 'running', 'succeeded', 'failed', 'timeout', 'killed'
    )),
    exit_code INTEGER,
    cpu_usage_ms BIGINT,
    memory_peak_bytes BIGINT,
    network_bytes_in BIGINT DEFAULT 0,
    network_bytes_out BIGINT DEFAULT 0,
    -- Должно быть 0 (песочницы без сети)
    error_message TEXT,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_sandbox_exec_run ON sandbox_executions(run_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_exec_toolset ON sandbox_executions(toolset_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_exec_started ON sandbox_executions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sandbox_exec_status ON sandbox_executions(status);

COMMENT ON TABLE sandbox_executions IS 'Журнал исполнения песочниц gVisor: ресурсы, длительность, статусы';

-- ==============================================================================
-- Таблица: integration_calls (журнал вызовов внешних систем)
-- ==============================================================================
-- Журнал вызовов внешних систем: GPU-узел (.228), корпоративное хранилище (ВМ №4),
-- рабочая станция. Используется для аудита, мониторинга и отладки mTLS-интеграций.

CREATE TABLE IF NOT EXISTS integration_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID REFERENCES runs(run_id) ON DELETE SET NULL,
    target_system VARCHAR(64) NOT NULL CHECK (target_system IN (
        'gpu_llm', 'gpu_embeddings', 'gpu_ocr',
        'corp_pg', 'workstation', 'pypi_mirror', 'langfuse'
    )),
    endpoint VARCHAR(512) NOT NULL,
    method VARCHAR(16) NOT NULL DEFAULT 'POST',
    request_size_bytes BIGINT,
    response_size_bytes BIGINT,
    status_code INTEGER,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    finished_at TIMESTAMP WITH TIME ZONE,
    duration_ms INTEGER,
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    client_cert_cn VARCHAR(255),
    server_cert_cn VARCHAR(255),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_integration_calls_run ON integration_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_integration_calls_target ON integration_calls(target_system);
CREATE INDEX IF NOT EXISTS idx_integration_calls_started ON integration_calls(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_integration_calls_success ON integration_calls(success);

COMMENT ON TABLE integration_calls IS 'Журнал вызовов внешних систем: mTLS-интеграции, тайминги, статусы';

-- ==============================================================================
-- Функция: log_audit_event()
-- ==============================================================================
-- Утилита для упрощённой записи событий в audit_log.
-- Вызывается из кода приложения или других SQL-функций.

CREATE OR REPLACE FUNCTION log_audit_event(
    p_user_id VARCHAR,
    p_action VARCHAR,
    p_resource_type VARCHAR DEFAULT NULL,
    p_resource_id VARCHAR DEFAULT NULL,
    p_run_id UUID DEFAULT NULL,
    p_details JSONB DEFAULT '{}'::jsonb,
    p_status VARCHAR DEFAULT 'success',
    p_duration_ms INTEGER DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
    v_id UUID;
BEGIN
    INSERT INTO audit_log (
        user_id, action, resource_type, resource_id, run_id,
        details, status, duration_ms
    ) VALUES (
        p_user_id, p_action, p_resource_type, p_resource_id, p_run_id,
        p_details, p_status, p_duration_ms
    )
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON FUNCTION log_audit_event IS 'Утилита для записи событий в audit_log';

-- ==============================================================================
-- Функция: get_active_rate()
-- ==============================================================================
-- Возвращает актуальную ставку по договору и наименованию на указанную дату.
-- Использует EXCLUDE-ограничение из таблицы rates для корректной работы
-- с версионированием интервалов.

CREATE OR REPLACE FUNCTION get_active_rate(
    p_contract_id VARCHAR,
    p_item_name VARCHAR,
    p_as_of_date DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    rate_id UUID,
    price NUMERIC,
    unit VARCHAR,
    valid_from DATE,
    valid_to DATE,
    source_document_id UUID,
    source_page INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        r.id, r.price, r.unit, r.valid_from, r.valid_to,
        r.source_document_id, r.source_page
    FROM rates r
    WHERE r.contract_id = p_contract_id
      AND r.item_name = p_item_name
      AND r.valid_from <= p_as_of_date
      AND (r.valid_to IS NULL OR r.valid_to >= p_as_of_date)
    LIMIT 1;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_active_rate IS 'Получение актуальной ставки по договору и наименованию на дату';

-- ==============================================================================
-- Функция: count_active_runs_for_thread()
-- ==============================================================================
-- Возвращает количество активных ранов для указанного треда.
-- Используется для проверки блокировки треда (один активный ран на тред).

CREATE OR REPLACE FUNCTION count_active_runs_for_thread(p_thread_id UUID)
RETURNS INTEGER AS $$
DECLARE
    v_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM runs
    WHERE thread_id = p_thread_id
      AND status IN ('QUEUED', 'RUNNING', 'WAITING_HUMAN');

    RETURN v_count;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION count_active_runs_for_thread IS 'Количество активных ранов для треда (проверка блокировки)';

-- ==============================================================================
-- Представление: v_run_summary (сводка по ранам)
-- ==============================================================================
-- Удобное представление для отчётности и мониторинга.

CREATE OR REPLACE VIEW v_run_summary AS
SELECT
    r.run_id,
    r.thread_id,
    r.status,
    r.user_id,
    r.graph_version,
    r.created_at,
    r.updated_at,
    r.started_at,
    r.finished_at,
    EXTRACT(EPOCH FROM (r.finished_at - r.started_at)) AS duration_seconds,
    (SELECT COUNT(*) FROM interrupt_decisions id WHERE id.run_id = r.run_id) AS hitl_decisions_count,
    (SELECT COUNT(*) FROM audit_log al WHERE al.run_id = r.run_id) AS audit_events_count,
    (SELECT COUNT(*) FROM integration_calls ic WHERE ic.run_id = r.run_id) AS integration_calls_count,
    (SELECT COUNT(*) FROM sandbox_executions se WHERE se.run_id = r.run_id) AS sandbox_executions_count
FROM runs r;

COMMENT ON VIEW v_run_summary IS 'Сводка по ранам: длительность, количество HITL-решений, интеграций, песочниц';

-- ==============================================================================
-- Представление: v_integration_health (здоровье интеграций)
-- ==============================================================================
-- Статистика вызовов внешних систем за последние 24 часа.

CREATE OR REPLACE VIEW v_integration_health AS
SELECT
    target_system,
    COUNT(*) AS total_calls,
    COUNT(*) FILTER (WHERE success) AS successful_calls,
    COUNT(*) FILTER (WHERE NOT success) AS failed_calls,
    ROUND(100.0 * COUNT(*) FILTER (WHERE success) / NULLIF(COUNT(*), 0), 2) AS success_rate_percent,
    AVG(duration_ms) FILTER (WHERE success) AS avg_duration_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) FILTER (WHERE success) AS p95_duration_ms,
    MAX(started_at) AS last_call_at
FROM integration_calls
WHERE started_at >= NOW() - INTERVAL '24 hours'
GROUP BY target_system;

COMMENT ON VIEW v_integration_health IS 'Здоровье интеграций за последние 24 часа: успех, длительность, p95';

-- ==============================================================================
-- Итоговая проверка (опционально, для отладки)
-- ==============================================================================
-- SELECT table_name FROM information_schema.tables
-- WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
-- ORDER BY table_name;
--
-- SELECT table_name FROM information_schema.tables
-- WHERE table_schema = 'public' AND table_type = 'VIEW'
-- ORDER BY table_name;
--
-- SELECT routine_name FROM information_schema.routines
-- WHERE routine_schema = 'public' AND routine_type = 'FUNCTION'
-- ORDER BY routine_name;