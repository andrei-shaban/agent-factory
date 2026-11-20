# ==============================================================================
# Dockerfile — AG-UI Gateway (FastAPI)
# Production Blueprint v5.1 — ВМ №1
# ==============================================================================
# Единый образ с Worker (ВМ №2): разные entrypoints (uvicorn / taskiq worker).
# Python 3.14 — основная версия; при отсутствии бинарных wheels допускается
# fallback на 3.13 (изменить базовый образ).
# Кодировка: UTF-8 без BOM, окончания строк LF.
# ==============================================================================

# ------------------------------------------------------------------------------
# Базовый образ
# ------------------------------------------------------------------------------
# Основная версия — 3.14-slim. Если в внутреннем зеркале нет бинарных wheels
# для 3.14, заменить на python:3.13-slim (см. Blueprint v5.1 п. 3.6).
FROM python:3.14-slim AS base

# ------------------------------------------------------------------------------
# Метаданные (OCI labels)
# ------------------------------------------------------------------------------
LABEL org.opencontainers.image.title="factory-gateway"
LABEL org.opencontainers.image.description="AG-UI Gateway (FastAPI) — приём RunAgentInput, постановка задач в Redis, SSE-relay"
LABEL org.opencontainers.image.vendor="Factory Agents Platform"
LABEL org.opencontainers.image.version="1.0.0"

# ------------------------------------------------------------------------------
# Переменные окружения
# ------------------------------------------------------------------------------
# Отключаем создание .pyc-файлов и буферизацию stdout/stderr (важно для логов)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100

# Доверие к корпоративному Root CA (вшивается ниже через update-ca-certificates)
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Отключение телеметрии (Air-Gap, Blueprint v5.1 п. 2)
ENV LANGSMITH_TRACING=false \
    LANGCHAIN_TRACING_V2=false \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# Режим работы
ENV ENVIRONMENT=production \
    LOG_LEVEL=info

# ------------------------------------------------------------------------------
# Установка системных зависимостей и корпоративного Root CA
# ------------------------------------------------------------------------------
# ca-certificates — база для update-ca-certificates
# curl, wget — для healthcheck и диагностики
# build-essential, libpq-dev — для компиляции psycopg (если binary-wheel недоступен)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        wget \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------------------
# Корпоративный Root CA
# ------------------------------------------------------------------------------
# Монтируется из ./certs/corp-ca.crt при сборке (docker build --build-context).
# Если файл отсутствует — сборка упадёт с понятной ошибкой.
COPY certs/corp-ca.crt /usr/local/share/ca-certificates/corp-ca.crt
RUN update-ca-certificates

# ------------------------------------------------------------------------------
# Рабочая директория
# ------------------------------------------------------------------------------
WORKDIR /app

# ------------------------------------------------------------------------------
# Установка зависимостей Python
# ------------------------------------------------------------------------------
# Отдельный слой для кэширования — пересобирается только при изменении requirements.txt
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ------------------------------------------------------------------------------
# Код приложения
# ------------------------------------------------------------------------------
COPY app/ ./app/

# ------------------------------------------------------------------------------
# Создание непривилегированного пользователя
# ------------------------------------------------------------------------------
# UID 10001 — стандартный диапазон для service accounts
RUN groupadd --system --gid 10001 factory \
    && useradd --system --uid 10001 --gid factory --home /app --shell /bin/false factory \
    && chown -R factory:factory /app

USER factory:factory

# ------------------------------------------------------------------------------
# Healthcheck
# ------------------------------------------------------------------------------
# /health отвечает 200 без аутентификации — используется в docker-compose
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["wget", "--spider", "-q", "http://127.0.0.1:8000/health"]

# ------------------------------------------------------------------------------
# Порт
# ------------------------------------------------------------------------------
EXPOSE 8000

# ------------------------------------------------------------------------------
# Entrypoint по умолчанию — Gateway (uvicorn)
# ------------------------------------------------------------------------------
# Для запуска Worker (ВМ №2) переопределить команду:
#   docker run --entrypoint taskiq factory/gateway:latest worker app.main:broker --fs-discover
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--log-level", "info"]