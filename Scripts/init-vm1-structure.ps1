# init-vm1-structure.ps1
# Создание структуры директорий и файлов-пустышек для ВМ №1
# Production Blueprint v5.1
# Все файлы — пустые (0 байт), UTF-8 без BOM, окончания строк LF

$ErrorActionPreference = "Stop"
$RootDir = ".\vm1"

Write-Host "🚀 Инициализация структуры ВМ №1..." -ForegroundColor Cyan

# Создание корневой директории
if (-Not (Test-Path $RootDir)) {
    New-Item -ItemType Directory -Path $RootDir -Force | Out-Null
}

# ============================================================================
# 1. Директории
# ============================================================================
$Directories = @(
    "nginx\certs",
    "nginx\conf.d",
    "oauth2-proxy",
    "keycloak\import",
    "pypiserver\packages",
    "init-db",
    "spa\dist",
    "certs",
    "logs\nginx-audit",
    "gateway\app"
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

    # nginx
    "nginx\nginx.conf",
    "nginx\certs\.gitkeep",
    "nginx\conf.d\.gitkeep",

    # oauth2-proxy
    "oauth2-proxy\oauth2-proxy.cfg",

    # keycloak
    "keycloak\realm-export.json",
    "keycloak\import\.gitkeep",

    # pypiserver
    "pypiserver\packages\.gitkeep",

    # init-db
    "init-db\01-create-databases.sql",

    # spa
    "spa\dist\.gitkeep",

    # certs
    "certs\.gitkeep",

    # logs
    "logs\nginx-audit\.gitkeep",

    # gateway
    "gateway\Dockerfile",
    "gateway\requirements.txt",
    "gateway\app\__init__.py",
    "gateway\app\main.py"
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

Write-Host "`n✅ Структура ВМ №1 создана: $(Resolve-Path $RootDir)" -ForegroundColor Cyan
Write-Host "📊 Всего директорий: $($Directories.Count)" -ForegroundColor Cyan
Write-Host "📊 Всего файлов: $($Files.Count)" -ForegroundColor Cyan
Write-Host "`n💡 Следующий шаг: наполнить конфиги содержимым" -ForegroundColor Cyan