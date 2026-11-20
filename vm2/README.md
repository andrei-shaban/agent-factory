# ВМ №2 — Вычисления, песочницы, данные агентов

Production Blueprint v5.1

## Назначение

ВМ №2 — вычислительный узел платформы «Фабрика агентов и алгоритмов». Отвечает за:
- исполнение графов LangGraph (агенты, детерминированные алгоритмы, HITL-паузы);
- генерацию событий AG-UI и публикацию в Redis Streams;
- хранение runtime-данных фабрики: чекпоинты LangGraph, реестр ранов, RAG (pgvector);
- очередь ранов и шину событий (Redis фабрики);
- файловое хранилище платформы и blob Langfuse (SeaweedFS / S3);
- изоляцию недоверенного кода в песочницах gVisor (`runsc`);
- mTLS-интеграции с внешними системами: GPU-узел (.228), рабочая станция, корпоративное хранилище данных (ВМ №4).

ВМ №2 не принимает трафик от браузеров и не обслуживает периметр — это задача ВМ №1.

## Состав стека

| Сервис | Образ | Порт (внутри) | Сеть |
|---|---|---|---|
| Worker / LangGraph | `factory/gateway:latest` (единый образ с gateway, entrypoint: `taskiq worker`) | — | internal |
| PostgreSQL / pgvector — Runtime & RAG | `pgvector/pgvector:pg17` | 5432 | internal |
| Redis — фабрика | `redis:8-alpine` | 6379 | internal |
| SeaweedFS master | `chrislusf/seaweedfs:latest` | 9333 | internal |
| SeaweedFS volume | `chrislusf/seaweedfs:latest` | 8080 | internal |
| SeaweedFS filer | `chrislusf/seaweedfs:latest` | 8888 | internal |
| SeaweedFS S3 | `chrislusf/seaweedfs:latest` | 8333 | internal |
| Docker socket proxy | `tecnativa/docker-socket-proxy:latest` | 2375 (только для internal-контейнеров) | internal |

**Хостовые компоненты (не Docker-образы):**
- **gVisor runtime (`runsc`)** — runtime для песочниц, устанавливается на хост ВМ №2.
- **Wheel-cache** — локальный каталог на хосте, монтируется read-only в песочницы.

## Быстрый старт

```bash
# 1. Скопировать .env.example в .env и заполнить значения
cp .env.example .env
nano .env

# 2. Создать директории для сертификатов и данных
mkdir -p certs logs/worker logs/postgres logs/redis logs/seaweedfs
mkdir -p backups/wal-archive backups/pg-dump
mkdir -p wheel-cache sandbox/toolsets

# 3. Поместить mTLS-сертификаты (от корпоративного CA)
cp corp-ca.crt vm2-client.crt vm2-client.key certs/
chmod 0600 certs/vm2-client.key

# 4. Установить gVisor runtime на хост (один раз)
# https://gvisor.dev/docs/user_guide/install/
sudo apt-get install gvisor-runsc
# или загрузить из внутреннего зеркала

# 5. Загрузить Docker-образы (air-gap)
for f in /path/to/docker_images/*.tar; do docker load -i "$f"; done

# 6. Поднять стек
docker compose up -d

# 7. Проверить статус
docker compose ps
docker compose logs -f
```

## Проверка работоспособности

```bash
# PostgreSQL Runtime
docker exec vm2-postgres-runtime pg_isready -U factory_runtime -d factory

# Redis фабрики
docker exec vm2-redis-factory redis-cli -a <REDIS_FACTORY_PASSWORD> ping

# SeaweedFS
docker exec vm2-seaweedfs-master wget -q -O- http://127.0.0.1:9333/cluster/ps
docker exec vm2-seaweedfs-s3 wget -q -O- http://127.0.0.1:8333/

# Worker
docker exec vm2-worker pgrep -f taskiq

# Docker socket proxy (из контейнера worker)
docker exec vm2-worker wget -q -O- http://docker-socket-proxy:2375/_ping

# mTLS к GPU-узлу (.228)
docker exec vm2-worker curl -k --cert /etc/certs/vm2-client.crt \
    --key /etc/certs/vm2-client.key \
    https://gpu-node.corp.example.com:443/health

# mTLS к корпоративному хранилищу (ВМ №4)
docker exec vm2-worker curl -k --cert /etc/certs/vm2-client.crt \
    --key /etc/certs/vm2-client.key \
    https://vm4-postgres.corp.example.com:5432/
```

## Сети

- **internal** (bridge): все сервисы ВМ №2. Локальные вызовы между контейнерами (Worker → PostgreSQL, Worker → Redis, Worker → SeaweedFS) идут по внутренним DNS-именам без TLS (локально).

**Важно:** сеть `internal` не имеет флага `internal: true`, так как Worker должен обращаться к внешним узлам (.228, ВМ №4, рабочая станция, ВМ №1 для PyPI mirror и Langfuse). Межсетевой экран на хосте ВМ №2 ограничивает исходящий трафик согласно матрице маршрутов.

## Межсерверные маршруты (исходящие)

| Направление | Порт | Назначение |
|---|---|---|
| Worker → .228 | 443 | mTLS: LLM (`/`), embeddings (`/v1/embeddings`), OCR (`/ocr/`) |
| Worker → рабочая станция | выделенный | mTLS: выполнение бизнес-операций в корпоративных системах (Этап 2) |
| Worker → ВМ №4 (Корп. PG) | 5432 | mTLS: чтение мастер-данных, реестров, справочников (read-only) |
| Worker → ВМ №1 (PyPI mirror) | 8080 или 443 | загрузка wheels в локальный wheel-cache для песочниц |
| Worker → ВМ №1 (Langfuse web) | 443 через nginx или выделенный 3000 | инжест трейсов SDK, HTTPS + internal CA |
| Worker → NTP | 123 | синхронизация времени |

**Входящие маршруты (с ВМ №1):**

| Направление | Порт | Назначение |
|---|---|---|
| Gateway (ВМ №1) → Redis фабрики | 6379 | TLS + ACL `agui_svc`: постановка задач, `XREAD` событий, реконнект |
| Gateway (ВМ №1) → PostgreSQL Runtime | 5432 | TLS + `scram-sha-256`: чтение и запись статусов в реестре ранов |
| Langfuse (ВМ №1) → SeaweedFS S3 | 8333 | S3: blob Langfuse (events, media) |

## Примечания по безопасности

- **Air-Gap**: образы зафиксированы по digest, зависимости только из внутреннего зеркала, CDN запрещён, телеметрия отключена.
- **mTLS**: клиентские сертификаты воркера подписаны корпоративным CA; используются при обращении к .228, рабочей станции и ВМ №4. Ключи (`vm2-client.key`) изолированы на ВМ №2, права `0600`, монтируются в контейнер worker read-only.
- **Песочницы**: `runsc` (gVisor), `--network none`, read-only rootfs, tmpfs, `cap-drop ALL`, `no-new-privileges`, лимиты CPU/memory/pids, непривилегированный uid. Секреты внутрь не передаются. Песочницы не имеют сетевого доступа никуда: зависимости доставляются воркером через локальный wheel-cache (read-only том).
- **Docker socket proxy**: whitelist `CONTAINERS=1, POST=1, EXEC=1`. Доступ к `NETWORKS`, `VOLUMES`, `IMAGES`, `PRIVILEGED` запрещён. Прямой docker.sock в контейнеры не передаётся.
- **PostgreSQL Runtime**: отдельный том, жёсткое лимитирование памяти (`shared_buffers`, `work_mem`) для исключения конкуренции с Redis и песочницами. WAL-архивация в SeaweedFS.
- **Redis фабрики**: AOF включён (`appendonly yes`, `appendfsync everysec`), ACL `agui_svc`, жёсткий `MAXLEN ~ N` + TTL на Streams. Размещён локально к воркеру для минимизации round-trip при `XADD` на каждое событие.
- **Часовой пояс**: `TZ=Asia/Yekaterinburg` (Екатеринбург).
- **Кодировка**: все текстовые конфигурации — UTF-8 без BOM, окончания строк LF.

## Связь с Blueprint

Данный узел описан в разделе «2. Размещение и стек по узлам → ВМ №2» Production Blueprint v5.1. Межсерверные маршруты — в разделе «4. Межсерверные маршруты».

## Структура директорий

```
vm2/
├── docker-compose.yml
├── .env
├── .env.example
├── README.md
├── init-db/
│   ├── 01-create-databases.sql
│   ├── 02-enable-extensions.sql
│   └── 03-create-tables.sql
├── redis/
│   ├── redis.conf
│   └── acl.conf
├── seaweedfs/
│   ├── master.conf
│   ├── volume.conf
│   ├── filer.conf
│   └── s3.json
├── certs/
│   ├── corp-ca.crt         # корпоративный Root CA
│   ├── vm2-client.crt      # клиентский сертификат воркера
│   └── vm2-client.key      # приватный ключ (chmod 0600)
├── sandbox/
│   ├── Dockerfile.toolset
│   ├── entrypoint.sh
│   ├── requirements.txt
│   └── toolsets/           # предзапечённые toolset-образы
├── wheel-cache/            # локальный каталог wheels для песочниц
├── logs/
│   ├── worker/
│   ├── postgres/
│   ├── redis/
│   └── seaweedfs/
├── backups/
│   ├── wal-archive/        # WAL-архивация PostgreSQL
│   └── pg-dump/            # бэкапы pg_basebackup
└── worker/
    ├── app/
    │   ├── __init__.py
    │   ├── main.py
    │   ├── broker.py
    │   └── settings.py
    ├── graphs/             # определения графов LangGraph
    └── tools/              # инструменты (tools) для агентов
```

## Связанные узлы

- **ВМ №1** — периметр, шлюзы, observability, зеркала (Gateway, Langfuse, PyPI mirror).
- **.228** — GPU-узел инференса (LLM, embeddings, OCR) через mTLS.
- **ВМ №4** — корпоративное хранилище данных (мастер-данные, read-only для воркеров через mTLS).
- **Рабочая станция** — целевая корпоративная система для бизнес-операций (Этап 2, mTLS).
