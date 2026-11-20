-- ==============================================================================
-- Production Blueprint v5.1 — ВМ №2: PostgreSQL Runtime & RAG
-- Скрипт инициализации базы данных фабрики
-- Выполняется автоматически при первом запуске postgres-runtime
-- ==============================================================================
-- ВАЖНО: Замените пароли на реальные значения из .env перед первым запуском!
-- Или используйте переменные окружения через psql --set (см. документацию)
-- ==============================================================================

-- ==============================================================================
-- Пользователь Worker (для подключения из контейнера worker)
-- ==============================================================================
-- Создаётся до подключения к базе factory, так как POSTGRES_DB=factory
-- уже создаёт базу автоматически через переменную окружения

DO $$
BEGIN
    -- Создаём пользователя factory_worker, если он не существует
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_user WHERE usename = 'factory_worker') THEN
        CREATE USER factory_worker WITH ENCRYPTED PASSWORD 'CHANGE_ME_FACTORY_WORKER_PASSWORD';
    END IF;
END
$$;

-- ==============================================================================
-- Подключение к базе factory (создаётся автоматически через POSTGRES_DB)
-- ==============================================================================
\c factory

-- ==============================================================================
-- Расширения
-- ==============================================================================
-- pgvector — векторный поиск для RAG
CREATE EXTENSION IF NOT EXISTS vector;

-- ==============================================================================
-- Схема и права
-- ==============================================================================
-- Предоставляем полные права на схему public пользователю factory_worker
GRANT ALL ON SCHEMA public TO factory_worker;

-- Предоставляем права на все таблицы в схеме public
GRANT ALL ON ALL TABLES IN SCHEMA public TO factory_worker;

-- Предоставляем права на все последовательности
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO factory_worker;

-- ==============================================================================
-- Таблица: runs (реестр ранов фабрики)
-- ==============================================================================
-- Хранит информацию о каждом запуске графа: статус, владелец, временные метки.
-- Используется для восстановления очереди после рестарта шины (джаниор).

CREATE TABLE IF NOT EXISTS runs (
    run_id UUID PRIMARY KEY,
    thread_id UUID NOT NULL,
    status VARCHAR(32) NOT NULL CHECK (status IN (
        'QUEUED', 'RUNNING', 'WAITING_HUMAN', 'SUCCEEDED', 'FAILED', 'CANCELLED'
    )),
    user_id VARCHAR(255) NOT NULL,
    graph_version VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'::jsonb,
    error_message TEXT
);

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_runs_thread_id ON runs(thread_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_user_id ON runs(user_id);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);

-- Комментарий к таблице
COMMENT ON TABLE runs IS 'Реестр ранов фабрики: статус, владелец, временные метки, метаданные';

-- ==============================================================================
-- Таблица: thread_locks (блокировки тредов)
-- ==============================================================================
-- Обеспечивает единую блокировку: один активный ран на тред.
-- Используется для предотвращения параллельного исполнения графов в одном треде.

CREATE TABLE IF NOT EXISTS thread_locks (
    thread_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    locked_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    locked_by VARCHAR(255) NOT NULL
);

-- Индекс для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_thread_locks_run_id ON thread_locks(run_id);

-- Комментарий к таблице
COMMENT ON TABLE thread_locks IS 'Блокировки тредов: один активный ран на тред';

-- ==============================================================================
-- Таблица: interrupt_decisions (решения HITL)
-- ==============================================================================
-- Хранит решения человека на этапах interrupt() (Human-in-the-Loop).
-- Обеспечивает идемпотентность: двойное утверждение формы → один эффект.

CREATE TABLE IF NOT EXISTS interrupt_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    interrupt_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    decision JSONB NOT NULL,
    decided_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(run_id, interrupt_id)
);

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_interrupt_decisions_run_id ON interrupt_decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_interrupt_decisions_user_id ON interrupt_decisions(user_id);

-- Комментарий к таблице
COMMENT ON TABLE interrupt_decisions IS 'Решения HITL: идемпотентность утверждений форм';

-- ==============================================================================
-- Таблица: documents (документы и артефакты)
-- ==============================================================================
-- Хранит метаданные документов: драфты, финальные акты, договоры, допсоглашения.
-- Ссылки на файлы в SeaweedFS (S3).

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

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_documents_run_id ON documents(run_id);
CREATE INDEX IF NOT EXISTS idx_documents_contract_id ON documents(contract_id);
CREATE INDEX IF NOT EXISTS idx_documents_document_type ON documents(document_type);

-- Комментарий к таблице
COMMENT ON TABLE documents IS 'Метаданные документов: драфты, акты, договоры, ссылки на SeaweedFS';

-- ==============================================================================
-- Таблица: rates (Единый корпоративный реестр ставок — ЕРР)
-- ==============================================================================
-- Хранит ставки работ/материалов/оборудования по договорам.
-- Версионирование через valid_from / valid_to (интервалы действия).

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
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    -- Ограничение на пересечение интервалов действия для одной позиции
    EXCLUDE USING gist (
        contract_id WITH =,
        item_name WITH =,
        daterange(valid_from, valid_to, '[]') WITH &&
    )
);

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_rates_contract_id ON rates(contract_id);
CREATE INDEX IF NOT EXISTS idx_rates_item_name ON rates(item_name);
CREATE INDEX IF NOT EXISTS idx_rates_valid_from ON rates(valid_from);
CREATE INDEX IF NOT EXISTS idx_rates_valid_to ON rates(valid_to);

-- Комментарий к таблице
COMMENT ON TABLE rates IS 'Единый корпоративный реестр ставок (ЕРР): версионирование через интервалы действия';

-- ==============================================================================
-- Таблица: verifications (результаты сверки)
-- ==============================================================================
-- Хранит результаты сверки позиций актов со ставками ЕРР.
-- Включает доказательную базу: страница, ячейка, ожидаемое/фактическое значение.

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

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_verifications_run_id ON verifications(run_id);
CREATE INDEX IF NOT EXISTS idx_verifications_document_id ON verifications(document_id);
CREATE INDEX IF NOT EXISTS idx_verifications_rate_id ON verifications(rate_id);
CREATE INDEX IF NOT EXISTS idx_verifications_status ON verifications(status);

-- Комментарий к таблице
COMMENT ON TABLE verifications IS 'Результаты сверки позиций актов со ставками ЕРР: доказательная база (страница, ячейка)';

-- ==============================================================================
-- Таблица: embeddings (векторные представления для RAG)
-- ==============================================================================
-- Хранит векторные представления текста для семантического поиска.
-- Используется pgvector для хранения и поиска векторов.

CREATE TABLE IF NOT EXISTS embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding vector(1536), -- размерность зависит от модели эмбеддингов
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_embeddings_document_id ON embeddings(document_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_chunk_index ON embeddings(chunk_index);

-- Векторный индекс для семантического поиска (IVFFlat, списки=100)
CREATE INDEX IF NOT EXISTS idx_embeddings_embedding ON embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Комментарий к таблице
COMMENT ON TABLE embeddings IS 'Векторные представления текста для RAG: семантический поиск по документам';

-- ==============================================================================
-- Функция: update_updated_at_column()
-- ==============================================================================
-- Автоматически обновляет поле updated_at при изменении строки.

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ==============================================================================
-- Триггеры: автообновление updated_at
-- ==============================================================================
CREATE TRIGGER trigger_runs_updated_at
    BEFORE UPDATE ON runs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trigger_rates_updated_at
    BEFORE UPDATE ON rates
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ==============================================================================
-- Итоговая проверка (опционально, для отладки)
-- ==============================================================================
-- SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';
-- SELECT * FROM pg_extension WHERE extname = 'vector';