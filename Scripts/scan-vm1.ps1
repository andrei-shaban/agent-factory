#Requires -Version 5.1
<#
.SYNOPSIS
    Полное сканирование ВМ №1 с сохранением содержимого всех файлов в текстовый файл.

.DESCRIPTION
    Рекурсивно обходит vm1/, читает содержимое всех текстовых файлов
    и сохраняет результат в указанный файл (по умолчанию vm1-scan.txt).

.PARAMETER Path
    Путь к директории ВМ №1. По умолчанию — .\vm1

.PARAMETER OutputFile
    Путь к выходному файлу. По умолчанию — .\vm1-scan.txt

.EXAMPLE
    .\Scripts\scan-vm1.ps1
    .\Scripts\scan-vm1.ps1 -OutputFile .\reports\vm1-full-scan.txt
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Path = ".\vm1",

    [Parameter(Position = 1)]
    [string]$OutputFile = ".\vm1-scan.txt"
)

# UTF-8 вывод
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$RootPath = (Resolve-Path -Path $Path).Path
$OutputPath = (Resolve-Path -Path (Split-Path $OutputFile -Parent) -ErrorAction SilentlyContinue).Path
if (-not $OutputPath) {
    New-Item -ItemType Directory -Path (Split-Path $OutputFile -Parent) -Force | Out-Null
    $OutputPath = (Resolve-Path -Path (Split-Path $OutputFile -Parent)).Path
}
$FullOutputFile = Join-Path $OutputPath (Split-Path $OutputFile -Leaf)

Write-Host "🔍 Сканирование ВМ №1: $RootPath" -ForegroundColor Cyan
Write-Host "💾 Вывод будет сохранён в: $FullOutputFile" -ForegroundColor Cyan
Write-Host ""

# Исключения (бинарные файлы, пустые .gitkeep)
$ExcludeExtensions = @('.whl', '.pyc', '.pyo', '.so', '.dll', '.exe', '.jpg', '.jpeg', '.png', '.gif', '.ico', '.svg', '.woff', '.woff2', '.ttf', '.eot')
$ExcludeFiles = @('.gitkeep')

# Получаем все файлы
$files = Get-ChildItem -Path $RootPath -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Extension -notin $ExcludeExtensions -and
        $_.Name -notin $ExcludeFiles
    } |
    Sort-Object FullName

Write-Host "Найдено файлов для сканирования: $($files.Count)" -ForegroundColor Green
Write-Host ""

# Собираем вывод в массив строк
$output = @()
$output += "═══════════════════════════════════════════════════════════"
$output += "ОТЧЁТ ПО СКАНИРОВАНИЮ ВМ №1 (ПЕРИМЕТР)"
$output += "Дата: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
$output += "Корень: $RootPath"
$output += "Всего файлов: $($files.Count)"
$output += "═══════════════════════════════════════════════════════════"
$output += ""

$processedCount = 0
foreach ($file in $files) {
    $relative = $file.FullName.Substring($RootPath.Length).TrimStart('\')
    $size = $file.Length
    $processedCount++

    Write-Host "  [$processedCount / $($files.Count)] $relative ($size байт)" -ForegroundColor Gray

    $output += "═══════════════════════════════════════════════════════════"
    $output += "📄 ФАЙЛ: $relative"
    $output += "📊 Размер: $size байт"
    $output += "═══════════════════════════════════════════════════════════"
    $output += ""

    try {
        if ($size -eq 0) {
            $output += "(пустой файл)"
        } else {
            # Пытаемся прочитать как UTF-8 текст
            $content = [System.IO.File]::ReadAllText($file.FullName, [System.Text.Encoding]::UTF8)
            $output += $content
        }
    } catch {
        # Если UTF-8 не получился — пробуем default encoding
        try {
            $content = Get-Content -Path $file.FullName -Raw -ErrorAction Stop
            $output += $content
        } catch {
            $output += "⚠️  Не удалось прочитать файл: $($_.Exception.Message)"
        }
    }

    $output += ""
    $output += ""
}

$output += "═══════════════════════════════════════════════════════════"
$output += "✅ Сканирование завершено. Обработано файлов: $processedCount"
$output += "═══════════════════════════════════════════════════════════"

# Записываем в файл UTF-8 без BOM
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($FullOutputFile, $output, $utf8NoBom)

Write-Host ""
Write-Host "✅ Готово!" -ForegroundColor Green
Write-Host "   Файл сохранён: $FullOutputFile" -ForegroundColor Green
Write-Host "   Размер отчёта: $([math]::Round((Get-Item $FullOutputFile).Length / 1KB, 1)) KB" -ForegroundColor Green