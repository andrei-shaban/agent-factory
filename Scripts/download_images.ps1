# download_images.ps1
# Скрипт для выгрузки Docker-образов для Фабрики агентов (Air-Gap)

$ErrorActionPreference = "Continue"
$OutputDir = ".\docker_images"

# Проверка наличия Docker
if (-Not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Ошибка: Docker не найден. Убедитесь, что Docker Desktop или Docker Engine установлен и запущен." -ForegroundColor Red
    exit 1
}

# Создание директории для сохранения
if (-Not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

Write-Host "🚀 Начало выгрузки Docker-образов для Фабрики агентов (Air-Gap)..." -ForegroundColor Cyan
Write-Host "Целевая директория: $(Resolve-Path $OutputDir)`n" -ForegroundColor Cyan

# Массив образов согласно Production Blueprint v5.2 и GPU Blueprint
$Images = @(
    # --- ВМ №1: Периметр, шлюзы, observability, зеркала ---
    "nginx:1.28-alpine",
    "quay.io/oauth2-proxy/oauth2-proxy:v7.6.0",      # Стабильная версия из ветки 7.x
    "quay.io/keycloak/keycloak:26.0",                # Стабильная версия из ветки 26.x
    "node:22-slim",
    "redis:8-alpine",                                # Для Langfuse (ВМ №1)
    "postgres:17-alpine",                            # Auth & Observability (ВМ №1)
    "langfuse/langfuse:latest",                      # v4.x (уточните тег, например 4.82.0, при наличии в зеркале)
    "langfuse/langfuse-worker:latest",
    "clickhouse/clickhouse-server:26.8-alpine",      # Согласно Blueprint v5.2
    "pypiserver/pypiserver:latest",

    # --- ВМ №2: Вычисления, песочницы, данные агентов ---
    "python:3.13-slim",                              # Fallback для 3.14 согласно п. 3.6 Blueprint
    "pgvector/pgvector:pg17",
    "chrislusf/seaweedfs:latest",
    "tecnativa/docker-socket-proxy:latest"


)

foreach ($Image in $Images) {
    Write-Host "⬇️  Pulling: $Image" -ForegroundColor Yellow

    # Выгрузка образа
    docker pull $Image
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Ошибка при загрузке (pull) образа: $Image" -ForegroundColor Red
        continue
    }

    # Формирование безопасного имени файла (замена / и : на _)
    $SafeName = $Image -replace '[^a-zA-Z0-9._-]', '_'
    $TarFile = Join-Path $OutputDir "$SafeName.tar"

    Write-Host "💾 Saving: $Image -> $TarFile" -ForegroundColor Yellow

    # Сохранение образа в tar-архив
    docker save -o $TarFile $Image
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Ошибка при сохранении (save) образа: $Image" -ForegroundColor Red
        continue
    }

    $FileSizeMB = [math]::Round((Get-Item $TarFile).Length / 1MB, 2)
    Write-Host "✅ Успешно сохранён: $TarFile ($FileSizeMB МБ)" -ForegroundColor Green
    Write-Host "----------------------------------------"
}

Write-Host "🎉 Все доступные образы успешно сохранены в директорию: $(Resolve-Path $OutputDir)" -ForegroundColor Cyan
Write-Host "💡 Следующий шаг: перенесите эту директорию на целевые изолированные ВМ и используйте скрипт load_images.ps1" -ForegroundColor Cyan