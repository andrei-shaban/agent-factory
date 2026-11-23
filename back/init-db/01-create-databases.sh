#!/usr/bin/env bash
# Идемпотентная инициализация БД Keycloak и Langfuse для ВМ №1.
set -Eeuo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${KEYCLOAK_DB_USER:?KEYCLOAK_DB_USER is required}"
: "${KEYCLOAK_DB_PASSWORD:?KEYCLOAK_DB_PASSWORD is required}"
: "${LANGFUSE_DB_USER:?LANGFUSE_DB_USER is required}"
: "${LANGFUSE_DB_PASSWORD:?LANGFUSE_DB_PASSWORD is required}"

create_role() {
    local role="$1"
    local password="$2"

    psql -v ON_ERROR_STOP=1 \
        --username "$POSTGRES_USER" \
        --dbname "$POSTGRES_DB" \
        --set=role_name="$role" \
        --set=role_password="$password" <<'EOSQL'
SELECT format('CREATE ROLE %I LOGIN', :'role_name')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role_name')
\gexec
SELECT format('ALTER ROLE %I WITH LOGIN ENCRYPTED PASSWORD %L', :'role_name', :'role_password')
\gexec
EOSQL
}

create_database() {
    local database="$1"
    local owner="$2"

    psql -v ON_ERROR_STOP=1 \
        --username "$POSTGRES_USER" \
        --dbname "$POSTGRES_DB" \
        --set=db_name="$database" \
        --set=db_owner="$owner" <<'EOSQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'db_name', :'db_owner')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db_name')
\gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'db_name', :'db_owner')
\gexec
SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'db_name', :'db_owner')
\gexec
EOSQL

    psql -v ON_ERROR_STOP=1 \
        --username "$POSTGRES_USER" \
        --dbname "$database" \
        --set=db_owner="$owner" <<'EOSQL'
GRANT ALL ON SCHEMA public TO :"db_owner";
ALTER SCHEMA public OWNER TO :"db_owner";
EOSQL
}

echo 'Creating/updating Keycloak and Langfuse database roles.'
create_role "$KEYCLOAK_DB_USER" "$KEYCLOAK_DB_PASSWORD"
create_role "$LANGFUSE_DB_USER" "$LANGFUSE_DB_PASSWORD"

create_database keycloak "$KEYCLOAK_DB_USER"
create_database langfuse "$LANGFUSE_DB_USER"

echo 'Database initialization completed.'
