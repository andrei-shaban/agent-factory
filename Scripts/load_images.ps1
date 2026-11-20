# load_images.ps1
# Скрипт для загрузки Docker-образов в изолированном контуре (Air-Gap)

$ErrorActionPreference = "Continue"
$InputDir = ".\docker_images"

# Проверка наличия Docker
if (-Not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Ошибка: Docker не найден. Убедитесь, что Docker установлен и запущен." -ForegroundColor Red
    exit 1
}

# Проверка наличия директории с архивами
if (-Not (Test-Path $InputDir)) {
    Write-Host "❌ Директория $InputDir не найдена." -ForegroundColor Red
    Write-Host "Убедитесь, что вы перенесли папку docker_images с архивами образов на эту машину." -ForegroundColor Yellow
    exit 1
}

Write-Host "🚀 Начало загрузки Docker-образов в локальный реестр..." -ForegroundColor Cyan
Write-Host "Источник: $(Resolve-Path $InputDir)`n" -ForegroundColor Cyan

$TarFiles = Get-ChildItem -Path $InputDir -Filter "*.tar"

if ($TarFiles.Count -eq 0) {
    Write-Host "⚠️  В директории не найдено файлов .tar" -ForegroundColor Yellow
    exit 0
}

foreach ($TarFile in $TarFiles) {
    Write-Host "⬆️  Loading: $($TarFile.Name)" -ForegroundColor Yellow

    docker load -i $TarFile.FullName
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Ошибка при загрузке образа из: $($TarFile.Name)" -ForegroundColor Red
        continue
        }

    Write-Host "✅ Успешно загружен: $($TarFile.Name)" -ForegroundColor Green
    Write-Host "----------------------------------------"
}

Write-Host "🎉 Процесс загрузки завершён." -ForegroundColor Cyan
Write-Host "💡 Проверьте наличие образов командой: docker images" -ForegroundColor Cyan