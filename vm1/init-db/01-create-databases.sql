-- ==============================================================================
-- Production Blueprint v5.1 — ВМ №1: PostgreSQL Auth & Observability
-- Скрипт инициализации баз данных и пользователей
-- Выполняется автоматически при первом запуске postgres-auth
-- ==============================================================================
-- ВАЖНО: Замените пароли на реальные значения из .env перед первым запуском!
-- Или используйте переменные окружения через psql --set (см. документацию)
-- ==============================================================================

-- ==============================================================================
-- Keycloak Database
-- ==============================================================================
CREATE DATABASE keycloak;

CREATE USER keycloak WITH ENCRYPTED PASSWORD 'CHANGE_ME_KEYCLOAK_DB_PASSWORD';

-- Предоставляем полные права на базу keycloak
GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak;

-- Подключаемся к базе keycloak для настройки схемы
\c keycloak

-- Keycloak требует права на создание таблиц и управление схемами
GRANT ALL ON SCHEMA public TO keycloak;

-- Возвращаемся в postgres для создания следующих баз
\c postgres

-- ==============================================================================
-- Langfuse Database
-- ==============================================================================
CREATE DATABASE langfuse;

CREATE USER langfuse WITH ENCRYPTED PASSWORD 'CHANGE_ME_LANGFUSE_DB_PASSWORD';

-- Предоставляем полные права на базу langfuse
GRANT ALL PRIVILEGES ON DATABASE langfuse TO langfuse;

-- Подключаемся к базе langfuse для настройки схемы
\c langfuse

-- Langfuse требует права на создание таблиц и управление схемами
GRANT ALL ON SCHEMA public TO langfuse;

-- Возвращаемся в postgres
\c postgres

-- ==============================================================================
-- Factory Database (для реестра ранов и метаданных фабрики)
-- ==============================================================================
CREATE DATABASE factory;

CREATE USER factory_gateway WITH ENCRYPTED PASSWORD 'CHANGE_ME_FACTORY_DB_PASSWORD';

-- Предоставляем полные права на базу factory
GRANT ALL PRIVILEGES ON DATABASE factory TO factory_gateway;

-- Подключаемся к базе factory для настройки схемы
\c factory

-- Factory требует права на создание таблиц и управление схемами
GRANT ALL ON SCHEMA public TO factory_gateway;

-- Возвращаемся в postgres
\c postgres

-- ==============================================================================
-- Итоговая проверка (опционально, для отладки)
-- ==============================================================================
-- SELECT datname FROM pg_database WHERE datistemplate = false;
-- SELECT usename FROM pg_user;