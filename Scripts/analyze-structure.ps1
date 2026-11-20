#Requires -Version 5.1
<#
.SYNOPSIS
    Анализ структуры проекта Factory Agents Platform.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Path = ".",
    [ValidateSet("Tree", "Markdown", "Json", "Csv", "All")]
    [string]$OutputFormat = "Tree",
    [switch]$IncludeHash,
    [string[]]$ExcludePatterns = @()
)

# UTF-8 вывод
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$RootPath = (Resolve-Path -Path $Path).Path
Write-Host "🔍 Анализ структуры проекта: $RootPath" -ForegroundColor Cyan

# Исключения
$AllExcludes = @(
    "rendered", "logs", "backups", "node_modules", "__pycache__",
    ".git", ".venv", "venv", "*.whl", "*.pyc", "*.log",
    ".idea"
) + $ExcludePatterns

$allFiles = @()
$allDirs = @()

# Сканирование
$items = Get-ChildItem -Path $RootPath -Recurse -Force -ErrorAction SilentlyContinue

foreach ($item in $items) {
    $skip = $false
    foreach ($pattern in $AllExcludes) {
        if ($item.Name -like $pattern -or $item.FullName -like "*\$pattern\*") {
            $skip = $true
            break
        }
    }
    if ($skip) { continue }

    if ($item.PSIsContainer) {
        $allDirs += $item
    } else {
        $allFiles += $item
    }
}

# Статистика
$totalFiles = $allFiles.Count
$totalDirs = $allDirs.Count
$totalSize = ($allFiles | Measure-Object -Property Length -Sum).Sum

Write-Host ""
Write-Host "📊 Сводка:" -ForegroundColor Cyan
Write-Host "  Директорий: $totalDirs"
Write-Host "  Файлов: $totalFiles"
Write-Host "  Размер: $([math]::Round($totalSize / 1KB, 1)) KB"
Write-Host ""

# Дерево
Write-Host "📁 Структура:" -ForegroundColor Cyan
foreach ($file in $allFiles | Sort-Object FullName) {
    $relative = $file.FullName.Substring($RootPath.Length + 1)
    $size = $file.Length
    $sizeStr = if ($size -lt 1KB) { "$size B" } else { "$([math]::Round($size / 1KB, 1)) KB" }
    Write-Host "  📄 $relative ($sizeStr)"
}

Write-Host ""
Write-Host "✅ Готово" -ForegroundColor Green