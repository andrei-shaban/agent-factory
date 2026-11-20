# init-vm2-structure.ps1
# Создание структуры директорий и файлов-пустышек для ВМ №2
# Production Blueprint v5.2
# Все файлы — пустые (0 байт), UTF-8 без BOM, окончания строк LF

$ErrorActionPreference = "Stop"
$RootDir = ".\vm2"

Write-Host "🚀 Инициализация структуры ВМ №2..." -ForegroundColor Cyan

# Создание корневой директории
if (-Not (Test-Path $RootDir)) {
    New-Item -ItemType Directory -Path $RootDir -Force | Out-Null
}

# ============================================================================
# 1. Директории
# ============================================================================
$Directories = @(
    # Инициализация БД
    "init-db",

    # Конфигурация Redis фабрики
    "redis",

    # Конфигурация SeaweedFS
    "seaweedfs",

    # mTLS-сертификаты для внешних интеграций
    "certs",

    # Песочницы (toolset-образы)
    "sandbox\toolsets",

    # Wheel-cache для песочниц (локальный каталог пакетов)
    "wheel-cache",

    # Логи
    "logs\worker",
    "logs\postgres",
    "logs\redis",
    "logs\seaweedfs",

    # Бэкапы (WAL-архивация PostgreSQL)
    "backups\wal-archive",
    "backups\pg-dump",

    # Worker (единый Python-образ с gateway)
    "worker\app",
    "worker\graphs",
    "worker\tools"
)

foreach ($dir in $Directories) {
    $fullPath = Join-Path $RootDir $dir
    if (-Not (Test-Path $fullPath)) {
        New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
        Write-Host "  📁 $dir/" -ForegroundColor Yellow
    }
}

# ============================================================================
# 2. Файлы-пустышки (0 байт)
# ============================================================================
$Files = @(
    # Корневые конфиги
    "docker-compose.yml",
    ".env",
    ".env.example",
    "README.md",

    # Инициализация PostgreSQL Runtime & RAG
    "init-db\01-create-databases.sql",
    "init-db\02-enable-extensions.sql",
    "init-db\03-create-tables.sql",

    # Конфигурация Redis фабрики
    "redis\redis.conf",
    "redis\acl.conf",

    # Конфигурация SeaweedFS
    "seaweedfs\master.conf",
    "seaweedfs\volume.conf",
    "seaweedfs\filer.conf",

    # mTLS-сертификаты (пустые плейсхолдеры)
    "certs\.gitkeep",
    "certs\corp-ca.crt",
    "certs\vm2-client.crt",
    "certs\vm2-client.key",

    # Песочницы (toolset-образы)
    "sandbox\Dockerfile.toolset",
    "sandbox\entrypoint.sh",
    "sandbox\requirements.txt",
    "sandbox\toolsets\.gitkeep",

    # Wheel-cache
    "wheel-cache\.gitkeep",

    # Логи
    "logs\worker\.gitkeep",
    "logs\postgres\.gitkeep",
    "logs\redis\.gitkeep",
    "logs\seaweedfs\.gitkeep",

    # Бэкапы
    "backups\wal-archive\.gitkeep",
    "backups\pg-dump\.gitkeep",

    # Worker (единый Python-образ с gateway)
    "worker\app\__init__.py",
    "worker\app\main.py",
    "worker\app\broker.py",
    "worker\app\settings.py",
    "worker\graphs\.gitkeep",
    "worker\tools\.gitkeep"
)

foreach ($file in $Files) {
    $fullPath = Join-Path $RootDir $file
    $dir = Split-Path $fullPath -Parent

    # Создаём директорию, если её нет
    if (-Not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }

    # Создаём пустой файл (0 байт)
    if (-Not (Test-Path $fullPath)) {
        New-Item -ItemType File -Path $fullPath -Force | Out-Null
        Write-Host "  📄 $file" -ForegroundColor Green
    }
}

Write-Host "`n✅ Структура ВМ №2 создана: $(Resolve-Path $RootDir)" -ForegroundColor Cyan
Write-Host "📊 Всего директорий: $($Directories.Count)" -ForegroundColor Cyan
Write-Host "📊 Всего файлов: $($Files.Count)" -ForegroundColor Cyan
Write-Host "`n💡 Следующий шаг: наполнить конфиги содержимым" -ForegroundColor Cyan