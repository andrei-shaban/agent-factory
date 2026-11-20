# ВМ №1 — Периметр, шлюзы, observability, зеркала

Production Blueprint v5.1

## Назначение

ВМ №1 — периметровый узел платформы «Фабрика агентов и алгоритмов». Отвечает за:
- приём HTTPS-трафика от браузеров и аутентификацию через Keycloak + oauth2-proxy;
- раздачу статического SPA (React + AG-UI);
- SSE-проксирование событий AG-UI от шлюза к клиентам;
- observability (Langfuse v4: web + worker + ClickHouse + Redis);
- зеркалирование PyPI для песочниц ВМ №2.

ВМ №1 не исполняет графы агентов и не хранит runtime-данные фабрики (чекпоинты, реестр ранов) — это задача ВМ №2.

## Состав стека

| Сервис | Образ | Порт (внутри) | Сеть |
|---|---|---|---|
| nginx (ingress + SSE-proxy) | `nginx:1.28-alpine` | 443, 80 | frontend |
| oauth2-proxy | `quay.io/oauth2-proxy/oauth2-proxy:v7.6.0` | 4180 | frontend |
| Keycloak | `quay.io/keycloak/keycloak:26.0` | 8080 | frontend, internal |
| PostgreSQL (Auth & Observability) | `postgres:17-alpine` | 5432 | internal |
| Redis (Langfuse) | `redis:8-alpine` | 6379 | internal |
| Langfuse web | `langfuse/langfuse:2` | 3000 | frontend, internal |
| Langfuse worker | `langfuse/langfuse-worker:2` | — | internal |
| ClickHouse | `clickhouse/clickhouse-server:24.12-alpine` | 8123 | internal |
| PyPI mirror | `pypiserver/pypiserver:latest` | 8080 | internal |
| AG-UI Gateway | `factory/gateway:latest` | 8000 | frontend, internal |

## Быстрый старт

```bash
# 1. Скопировать .env.example в .env и заполнить значения
cp .env.example .env
nano .env

# 2. Создать директории для сертификатов и артефактов
mkdir -p nginx/certs spa/dist certs logs/nginx-audit

# 3. Поместить TLS-сертификаты (от корпоративного CA)
cp server.crt server.key nginx/certs/
cp corp-ca.crt certs/

# 4. Поместить статику SPA (после сборки на CI)
cp -r /path/to/spa/dist/* spa/dist/

# 5. Загрузить Docker-образы (air-gap)
for f in /path/to/docker_images/*.tar; do docker load -i "$f"; done

# 6. Собрать образ gateway (если не загружен из таров)
cd gateway && docker build -t factory/gateway:latest . && cd ..

# 7. Поднять стек
docker compose up -d

# 8. Проверить статус
docker compose ps
docker compose logs -f
```

## Проверка работоспособности

```bash
# Healthcheck nginx
curl -k https://localhost/health

# Keycloak (через nginx)
curl -k https://localhost/auth/health/ready

# Langfuse UI
curl -k https://localhost/langfuse/api/public/health

# OAuth2-proxy ping (внутренний)
docker exec vm1-oauth2-proxy wget -q -O- http://127.0.0.1:4180/ping

# PostgreSQL
docker exec vm1-postgres-auth pg_isready -U factory_auth

# Redis Langfuse
docker exec vm1-redis-langfuse redis-cli -a <REDIS_LANGFUSE_PASSWORD> ping
```

## Сети

- **frontend** (bridge): nginx, oauth2-proxy, keycloak, langfuse-web, gateway. Имеет доступ наружу через хост (порт 443).
- **internal** (internal: true): postgres-auth, redis-langfuse, clickhouse, langfuse-worker, pypiserver, gateway, keycloak. Нет прямого выхода наружу — только через контейнеры frontend-сети.

Все БД и Redis доступны ТОЛЬКО контейнерам внутри ВМ №1. Порт 5432 postgres-auth не публикуется на хост.

## Межсерверные маршруты (исходящие)

| Направление | Порт | Назначение |
|---|---|---|
| gateway → vm2-redis-factory | 6379 | Redis фабрики (TLS + ACL `agui_svc`) |
| gateway → vm2-postgres-runtime | 5432 | PG runtime (TLS + `scram-sha-256`) |
| langfuse-web/worker → vm2-seaweedfs | 8333 | S3 blob (Langfuse events) |
| keycloak → LDAP/AD | 636 | федерация пользователей и групп |
| pypiserver | 8080 | приём запросов от воркеров ВМ №2 |

## Примечания по безопасности

- **Air-Gap**: образы зафиксированы по digest, зависимости только из внутреннего зеркала, CDN запрещён, телеметрия отключена.
- **mTLS**: клиентские сертификаты воркеров подписаны корпоративным CA; используются при обращении к .228, рабочей станции и ВМ №4.
- **Заголовки аутентификации**: `X-Forwarded-User` / `X-Forwarded-Groups` выставляются только из `auth_request_set`; входящие значения обнуляются в nginx до делегирования oauth2-proxy.
- **Часовой пояс**: `TZ=Asia/Yekaterinburg` (Екатеринбург).
- **Кодировка**: все текстовые конфигурации — UTF-8 без BOM, окончания строк LF.

## Связь с Blueprint

Данный узел описан в разделе «2. Размещение и стек по узлам → ВМ №1» Production Blueprint v5.1. Межсерверные маршруты — в разделе «4. Межсерверные маршруты».

## Структура директорий

```
vm1/
├── docker-compose.yml
├── .env
├── .env.example
├── README.md
├── nginx/
│   ├── nginx.conf
│   └── certs/              # server.crt, server.key (TLS)
├── oauth2-proxy/
│   └── oauth2-proxy.cfg
├── keycloak/
│   └── realm-export.json   # импорт realm (опционально)
├── pypiserver/
│   └── packages/           # wheels для песочниц ВМ №2
├── init-db/
│   └── 01-create-databases.sql
├── spa/
│   └── dist/               # статика SPA (артефакт сборки)
├── certs/
│   └── corp-ca.crt         # корпоративный Root CA
├── logs/
│   └── nginx-audit/
└── gateway/
    ├── Dockerfile
    ├── requirements.txt
    └── app/
        ├── __init__.py
        └── main.py
```

## Связанные узлы

- **ВМ №2** — вычисления, песочницы, данные агентов (Worker, Redis фабрики, PG runtime, SeaweedFS, Sandbox).
- **.228** — GPU-узел инференса (LLM, embeddings, OCR) через mTLS.
- **ВМ №4** — корпоративное хранилище данных (мастер-данные, read-only для воркеров через mTLS).
```