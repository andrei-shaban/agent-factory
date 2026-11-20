# ВМ №1 — Периметр, шлюзы, observability, зеркала
Production Blueprint v5.2

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
| Langfuse web | `langfuse/langfuse:4` | 3000 | frontend, internal |
| Langfuse worker | `langfuse/langfuse-worker:4` | — | internal |
| ClickHouse | `clickhouse/clickhouse-server:26.8-alpine` | 8123 | internal |
| PyPI mirror | `pypiserver/pypiserver` (пин) | 8080 | internal |
| AG-UI Gateway | `factory/gateway:latest` | 8000 | frontend |

**Важно про версии:**
- **Langfuse** — v4 (web + worker), по Blueprint §3.6/§5.7.
- **ClickHouse** — 26.8 LTS (минимум 25.12), по Blueprint §5.7.
- **PyPI mirror** — версия фиксируется по digest из зеркала (не `latest`), по Blueprint §5.1.
- **Все образы** — в проде фиксируются по digest из внутреннего зеркала (см. комментарии в `docker-compose.yml`).

## Быстрый старт

```bash
# 1. Скопировать .env.example в .env и заполнить значения
#    Обязательно задать VM1_HOST_IP и VM2_HOST_IP — адреса хостов для межсерверных маршрутов
cp .env.example .env
nano .env

# 2. Создать директории для сертификатов и артефактов
mkdir -p nginx/certs spa/dist certs rendered

# 3. Поместить TLS-сертификаты (от корпоративного УЦ).
#    Единый сертификат должен содержать в SAN ОБА имени:
#      - factory.corp.example.com   (SPA, API, Keycloak на /auth, oauth2-proxy)
#      - langfuse.corp.example.com  (Langfuse UI)
cp server.crt server.key nginx/certs/
cp corp-ca.crt certs/

# 4. Поместить статику SPA (после сборки на CI)
cp -r /path/to/spa/dist/* spa/dist/

# 5. Отрендерить конфигурацию (подстановка секрета клиента в репрезентацию
#    реалма, очистка служебных _*-ключей) -> ./rendered/realm-export.json
./render-configs.sh

# 6. Загрузить Docker-образы (air-gap)
for f in /path/to/docker_images/*.tar; do docker load -i "$f"; done

# 7. Собрать образ gateway (если не загружен из таров).
#    Сборка из КОРНЯ ВМ №1 (контекст = ./), см. раздел «Сборка образа».
docker build -f gateway/Dockerfile -t factory/gateway:latest .

# 8. Поднять стек
docker compose up -d

# 9. Проверить статус
docker compose ps
docker compose logs -f
```

## Сборка образа

Образ `factory/gateway` — единый образ для Gateway и (в перспективе) Worker,
согласно Blueprint §3.6. Сборка выполняется из корня ВМ №1 (`./`), а не из
папки `gateway/`, чтобы были доступны:
- `./gateway/requirements.txt` — зависимости;
- `./gateway/app/` — код приложения.

```bash
# Базовая сборка (из корня ВМ №1)
docker build -f gateway/Dockerfile -t factory/gateway:latest .

# Продакционная сборка: фиксация базового образа по digest + зеркало PyPI
docker build -f gateway/Dockerfile \
  --build-arg BASE_IMAGE=python:3.14-slim@sha256:<digest-из-зеркала> \
  --build-arg PIP_INDEX_URL=http://vm1-pypiserver:8080/simple/ \
  -t factory/gateway:latest .
```

Dockerfile использует многоступенчатую сборку и кэширование: скачанные `.deb`
и пакеты pip кэшируются между сборками; изменения кода не инвалидируют слой
с установкой зависимостей. Состав контекста ограничен `.dockerignore`
(секреты и отрендеренные конфиги не отправляются в демон).

**Принцип автономности артефактов (Blueprint §1.8):** в образ вшиваются только
базовый рантайм, системные пакеты и код приложения. Криптографические материалы
(корпоративный Root CA), конфигурации и секреты поставляются отдельно — через
bind-mount из хостовой системы или переменные окружения.

## Проверка работоспособности

Проверки ниже предполагают, что домены резолвятся (в `/etc/hosts` или DNS) на
адрес ВМ №1. Для локальной проверки подмените резолв через `--resolve`.

```bash
# Healthcheck nginx (порт 80, без аутентификации)
curl -k https://localhost/health

# Keycloak health (path-based, на домене фабрики)
curl -k --resolve factory.corp.example.com:443:127.0.0.1 \
  https://factory.corp.example.com/auth/health/ready

# Langfuse UI health (отдельный домен)
curl -k --resolve langfuse.corp.example.com:443:127.0.0.1 \
  https://langfuse.corp.example.com/api/public/health

# Langfuse health напрямую (выделенный порт, без аутентификации)
curl -s http://${VM1_HOST_IP}:3000/api/public/health

# PyPI mirror (выделенный порт для ВМ №2)
curl -s http://${VM1_HOST_IP}:8080/

# OAuth2-proxy ping (внутренний)
docker exec vm1-oauth2-proxy wget -q -O- http://127.0.0.1:4180/ping

# PostgreSQL
docker exec vm1-postgres-auth pg_isready -U factory_auth

# Redis Langfuse
docker exec vm1-redis-langfuse redis-cli -a "$REDIS_LANGFUSE_PASSWORD" ping
```

## Сети

- **frontend** (bridge): nginx, oauth2-proxy, keycloak, langfuse-web, gateway.
  Имеет доступ наружу через хост (порт 443).
- **internal** (internal: true): postgres-auth, redis-langfuse, clickhouse,
  langfuse-worker, pypiserver, keycloak. Нет прямого выхода наружу — только
  через контейнеры frontend-сети.

Изоляция по Blueprint §3.1:
- PostgreSQL ВМ №1 обслуживает только базы Keycloak и Langfuse (реестр
  ранов фабрики — на ВМ №2). Внешний порт 5432 на хосте закрыт.
- Redis (Langfuse) доступен только контейнерам Langfuse.
- Gateway размещён только во `frontend`: ему не нужен доступ к
  `redis-langfuse`, `clickhouse` и `postgres-auth`.

## Межсерверные маршруты

### Исходящие (с ВМ №1)

| Направление | Порт | Назначение |
|---|---|---|
| gateway → ВМ №2 (Redis фабрики) | 6379 | постановка задач, `XREAD` событий (TLS + ACL `agui_svc`) |
| gateway → ВМ №2 (PG runtime) | 5432 | реестр ранов (TLS + `scram-sha-256`) |
| langfuse-web/worker → ВМ №2 (SeaweedFS) | 8333 | S3 blob (Langfuse events, path-style) |
| keycloak → LDAP/AD | 636 | федерация пользователей и групп |

### Входящие (на ВМ №1, из ВМ №2)

На эти порты образуются маршруты с ВМ №2. Доступ ограничен адресом ВМ №2
на межсетевом экране (публикация идёт на `VM1_HOST_IP`, а не `0.0.0.0`):

| Сервис ВМ №1 | Порт на хосте | Назначение |
|---|---|---|
| langfuse-web | `${VM1_HOST_IP}:3000` | инжест трейсов (воркер ВМ №2, API-ключи) |
| pypiserver | `${VM1_HOST_IP}:8080` | загрузка wheels в кэш песочниц |

Конкретные адреса ВМ №2 задаются переменной `VM2_HOST_IP` в `.env`.

## Примечания по безопасности

- **Air-Gap:** образы зафиксированы по digest, зависимости только из внутреннего
  зеркала, CDN запрещён, телеметрия отключена.
- **Единый образ (Blueprint §3.6):** Gateway и (в перспективе) Worker собираются
  из одного образа с разными точками входа (`uvicorn` / `taskiq worker`).
- **Принцип автономности артефактов (Blueprint §1.8):** Docker-образы — статичные
  рантайм-контейнеры. Корпоративный Root CA **монтируется в контейнер gateway
  через volume** (`./certs/corp-ca.crt:/etc/ssl/certs/corp-ca.crt:ro`),
  переменные `SSL_CERT_FILE` и `REQUESTS_CA_BUNDLE` указывают на смонтированный
  файл. Это исключает вшивание криптографических материалов в слои образа и
  позволяет ротировать CA без пересборки.
- **Аутентификация:** `X-Forwarded-User/Groups` выставляются только из
  `auth_request_set`; входящие значения обнуляются в nginx до делегирования
  в бэкенд. 401 для API отдаётся честным 401 (без 302-редиректа) — редирект
  на страницу входа выполняет SPA.
- **Keycloak:** работает в path-based режиме (`/auth/`), публично через
  `location /auth/` без `auth_request`. Realm импортируется из
  `rendered/realm-export.json` при первом запуске (`--import-realm`).
- **Langfuse UI:** отдаётся отдельным vhost'ом (не под subpath), защищён
  `auth_request`; инжест с ВМ №2 идёт напрямую на выделенный порт 3000.
- **Секреты:** не хранятся в `oauth2-proxy.cfg` (передаются через переменные
  окружения), не попадают в контекст сборки образа (`.dockerignore`), не
  коммитятся (`rendered/` и `.env` исключены из системы контроля версий).
- **Часовой пояс:** `TZ=Asia/Yekaterinburg` для всех контейнеров.
- **Кодировка:** все текстовые конфигурации — UTF-8 без BOM, окончания строк LF.

## Рендеринг конфигурации

`render-configs.sh` подставляет переменные окружения из `.env` в шаблон
репрезентации реалма (`keycloak/realm-export.json`) и вычищает служебные
`_*`-ключи, находящиеся вне схемы. Результат — `rendered/realm-export.json`,
который монтируется в контейнер Keycloak при первом запуске.

Секрет клиента `factory-spa` в шаблоне указан как `${OAUTH2_PROXY_CLIENT_SECRET}`
и подставляется из `.env`. Перед запуском убедитесь, что значение в `.env`
совпадает с секретом клиента в репрезентации (или обновите репрезентацию в
`render-configs.sh`-совместимом формате).

## Связь с Blueprint

Данный узел описан в разделе «2. Размещение и стек по узлам → ВМ №1»
Production Blueprint v5.2. Межсерверные маршруты — в разделе «4. Межсерверные
маршруты». Требования к Langfuse (v4, ClickHouse ≥25.12, SDK) — в разделах
«3.6. Базовый рантайм и наблюдаемость» и «5.7». Единый образ и сборка — в
«3.6» и «5.5». Принцип автономности артефактов — в «1. Ключевые архитектурные
принципы».

## Структура директорий

```
vm1/
 ├── docker-compose.yml
 ├── .env                       # секреты (из .env.example)
 ├── .env.example
 ├── .dockerignore              # ограничение контекста сборки образа
 ├── README.md
 ├── render-configs.sh          # рендеринг конфигурации (репрезентация реалма)
 ├── rendered/                  # отрендеренные конфиги (НЕ коммитить)
 │   └── realm-export.json
 ├── nginx/
 │   ├── nginx.conf
 │   └── certs/                 # server.crt, server.key (TLS, SAN на оба домена)
 ├── oauth2-proxy/
 │   └── oauth2-proxy.cfg       # статический, секреты через переменные окружения
 ├── keycloak/
 │   ├── realm-export.json      # шаблон репрезентации (секрет = ${...})
 │   └── LDAP-FEDERATION.md     # шаблон настройки федерации
 ├── pypiserver/
 │   └── packages/              # wheels для песочниц ВМ №2
 ├── init-db/
 │   └── 01-create-databases.sql.sh   # создание БД из переменных окружения
 ├── spa/
 │   └── dist/                  # статика SPA (артефакт сборки)
 ├── certs/
 │   └── corp-ca.crt            # корпоративный Root CA (монтируется в gateway через volume)
 └── gateway/
     ├── Dockerfile             # многоступенчатая сборка, контекст = корень ВМ №1
     ├── requirements.txt
     └── app/
         ├── __init__.py
         └── main.py
```

## Связанные узлы

- **ВМ №2** — вычисления, песочницы, данные агентов (Worker, Redis фабрики,
  PG runtime, SeaweedFS, Sandbox). Реестр ранов фабрики хранится именно там.
- **ВМ №4** — корпоративное хранилище данных (мастер-данные, read-only для
  воркеров через mTLS).
- **GPU-узел** — инференс (LLM, embeddings, OCR) через mTLS. Автономный узел,
  в состав данного проекта не входит; ВМ №1 может обращаться к нему опционально
  (метрики/здоровье) через отдельный клиентский сертификат.
```