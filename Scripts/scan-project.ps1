<#
.SYNOPSIS
    Полное сканирование структуры и содержимого проекта "Фабрика агентов".
.DESCRIPTION
    Генерирует Markdown-отчёт с деревом каталогов, содержимым файлов,
    статистикой и состоянием окружения (Docker, Python, Git).
    Корректно работает с UTF-8 (без BOM), исключает мусорные директории.
.NOTES
    Author: Andrei Shaban
    Version: 1.0
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ProjectPath = "D:\agent-factory\vm1",

    [Parameter(Mandatory = $false)]
    [string]$OutputDir = "D:\agent-factory\vm1\scans",

    [Parameter(Mandatory = $false)]
    [long]$MaxFileSizeBytes = 1MB,

    [Parameter(Mandatory = $false)]
    [switch]$SkipDockerCheck,

    [Parameter(Mandatory = $false)]
    [switch]$SkipGitCheck
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $OutputEncoding

# ?????????????????????????????????????????????????????????????
# Конфигурация исключений
# ?????????????????????????????????????????????????????????????
$ExcludeDirs = @(
    '\.venv$',
    '\\\.venv\\',
    '\\node_modules\\',
    '\\__pycache__\\',
    '\\\.git\\',
    '\\\.mypy_cache\\',
    '\\\.pytest_cache\\',
    '\\\.ruff_cache\\',
    '\\dist\\',
    '\\build\\',
    '\\\.next\\',
    '\\\.cache\\',
    '\\scans$'
)

$ExcludeFiles = @(
    '\.pyc$',
    '\.pyo$',
    '\.dll$',
    '\.exe$',
    '\.so$',
    '\.o$',
    '\.bin$',
    '\.lock$',
    '\\package-lock\.json$',
    '\\yarn\.lock$',
    '\\poetry\.lock$'
)

# Расширения, содержимое которых читаем
$TextExtensions = @(
    '.py', '.md', '.yaml', '.yml', '.json', '.toml', '.ini', '.cfg',
    '.env', '.txt', '.sh', '.ps1', '.bat', '.cmd',
    '.html', '.css', '.js', '.ts', '.tsx', '.jsx',
    '.sql', '.xml', '.conf', '.properties',
    '.dockerfile', '.gitignore', '.editorconfig'
)

# Имена файлов без расширения, которые тоже читаем
$TextFileNames = @(
    'Dockerfile', 'Makefile', 'Vagrantfile', '.gitignore',
    '.dockerignore', '.editorconfig', '.env.example', '.env.template'
)

# ?????????????????????????????????????????????????????????????
# Утилиты
# ?????????????????????????????????????????????????????????????
function Write-Status {
    param([string]$Message, [string]$Color = "Cyan")
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] " -NoNewline -ForegroundColor DarkGray
    Write-Host $Message -ForegroundColor $Color
}

function Test-Excluded {
    param([string]$Path, [string[]]$Patterns)
    foreach ($pattern in $Patterns) {
        if ($Path -match $pattern) { return $true }
    }
    return $false
}

function Get-FileEncoding {
    param([string]$FilePath)
    try {
        $bytes = [System.IO.File]::ReadAllBytes($FilePath)
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            return "UTF-8-BOM"
        }
        if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
            return "UTF-16-LE"
        }
        if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF) {
            return "UTF-16-BE"
        }
        # Попытка определить валидность UTF-8
        try {
            $null = [System.Text.Encoding]::UTF8.GetString($bytes)
            $hasHighBytes = $bytes | Where-Object { $_ -gt 127 }
            if ($hasHighBytes) { return "UTF-8" }
            return "ASCII"
        }
        catch {
            return "Unknown/Binary"
        }
    }
    catch {
        return "Error"
    }
}

function Get-RelativePath {
    param([string]$FullPath, [string]$BasePath)
    return $FullPath.Substring($BasePath.Length).TrimStart('\', '/')
}

# ?????????????????????????????????????????????????????????????
# Основная логика сканирования
# ?????????????????????????????????????????????????????????????
Write-Status "Начало сканирования: $ProjectPath" "Green"

if (-not (Test-Path $ProjectPath)) {
    throw "Путь проекта не существует: $ProjectPath"
}

if (-not (Test-Path $OutputDir)) {
    $null = New-Item -ItemType Directory -Path $OutputDir -Force
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$reportPath = Join-Path $OutputDir "project-scan-$timestamp.md"

# Сбор всех элементов
Write-Status "Сканирование файловой системы..."
$allItems = Get-ChildItem -Path $ProjectPath -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object {
        $rel = Get-RelativePath $_.FullName $ProjectPath
        if ($_.PSIsContainer) {
            -not (Test-Excluded "\\$rel\\" $ExcludeDirs) -and -not (Test-Excluded "\\$rel" $ExcludeDirs)
        }
        else {
            -not (Test-Excluded "\\$rel" $ExcludeFiles)
        }
    }

$directories = $allItems | Where-Object { $_.PSIsContainer } | Sort-Object FullName
$files = $allItems | Where-Object { -not $_.PSIsContainer } | Sort-Object FullName

Write-Status "Найдено: $($directories.Count) каталогов, $($files.Count) файлов"

# ?????????????????????????????????????????????????????????????
# Построение дерева
# ?????????????????????????????????????????????????????????????
Write-Status "Построение дерева каталогов..."
$treeLines = [System.Collections.Generic.List[string]]::new()
$treeLines.Add("``````")
$treeLines.Add((Split-Path $ProjectPath -Leaf))

function Build-Tree {
    param([string]$Path, [string]$Prefix, [int]$Depth)

    if ($Depth -gt 10) { return }

    $children = Get-ChildItem -Path $Path -Force -ErrorAction SilentlyContinue |
        Where-Object {
            $rel = Get-RelativePath $_.FullName $ProjectPath
            if ($_.PSIsContainer) {
                -not (Test-Excluded "\\$rel\\" $ExcludeDirs) -and -not (Test-Excluded "\\$rel" $ExcludeDirs)
            }
            else {
                -not (Test-Excluded "\\$rel" $ExcludeFiles)
            }
        } | Sort-Object @{Expression={$_.PSIsContainer};Descending=$true}, Name

    $count = $children.Count
    $i = 0
    foreach ($child in $children) {
        $i++
        $isLast = ($i -eq $count)
        $connector = if ($isLast) { "??? " } else { "??? " }
        $icon = if ($child.PSIsContainer) { "? " } else { "? " }
        $size = if (-not $child.PSIsContainer) { " ($([math]::Round($child.Length / 1KB, 1)) KB)" } else { "" }
        $treeLines.Add("$Prefix$connector$icon$($child.Name)$size")

        if ($child.PSIsContainer) {
            $newPrefix = if ($isLast) { "$Prefix    " } else { "$Prefix?   " }
            Build-Tree -Path $child.FullName -Prefix $newPrefix -Depth ($Depth + 1)
        }
    }
}

Build-Tree -Path $ProjectPath -Prefix "" -Depth 0
$treeLines.Add("``````")

# ?????????????????????????????????????????????????????????????
# Чтение содержимого файлов
# ?????????????????????????????????????????????????????????????
Write-Status "Чтение содержимого файлов..."
$fileContents = [System.Collections.Generic.List[string]]::new()
$filesRead = 0
$filesSkipped = 0
$filesTooLarge = 0

foreach ($file in $files) {
    $ext = $file.Extension.ToLower()
    $name = $file.Name.ToLower()
    $relPath = Get-RelativePath $file.FullName $ProjectPath

    $isText = ($TextExtensions -contains $ext) -or ($TextFileNames -contains $file.Name)

    if (-not $isText) {
        $filesSkipped++
        continue
    }

    if ($file.Length -gt $MaxFileSizeBytes) {
        $fileContents.Add("### ? ``$relPath``")
        $fileContents.Add("> ?? Файл пропущен: размер $([math]::Round($file.Length / 1MB, 2)) MB превышает лимит $([math]::Round($MaxFileSizeBytes / 1MB, 2)) MB`n")
        $filesTooLarge++
        continue
    }

    $encoding = Get-FileEncoding $file.FullName

    try {
        $content = [System.IO.File]::ReadAllText($file.FullName, [System.Text.Encoding]::UTF8)
        $lineCount = ($content -split "`n").Count

        $fileContents.Add("### ? ``$relPath``")
        $fileContents.Add("**Размер:** $([math]::Round($file.Length / 1KB, 2)) KB | **Строк:** $lineCount | **Кодировка:** $encoding | **Изменён:** $($file.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))")
        $fileContents.Add("")
        $fileContents.Add("``````$($ext.TrimStart('.'))")
        $fileContents.Add($content)
        $fileContents.Add("``````")
        $fileContents.Add("")
        $filesRead++
    }
    catch {
        $fileContents.Add("### ? ``$relPath``")
        $fileContents.Add("> ? Ошибка чтения: $($_.Exception.Message)`n")
    }
}

# ?????????????????????????????????????????????????????????????
# Статистика
# ?????????????????????????????????????????????????????????????
Write-Status "Сбор статистики..."
$totalSize = ($files | Measure-Object -Property Length -Sum).Sum
$extStats = $files | Group-Object { $_.Extension.ToLower() } |
    Select-Object @{N='Ext';E={if($_.Name) {$_.Name} else {'(no ext)'}}}, Count, @{N='Size';E={($_.Group | Measure-Object Length -Sum).Sum}} |
    Sort-Object Size -Descending

$statsLines = [System.Collections.Generic.List[string]]::new()
$statsLines.Add("| Метрика | Значение |")
$statsLines.Add("|---------|----------|")
$statsLines.Add("| Всего каталогов | $($directories.Count) |")
$statsLines.Add("| Всего файлов | $($files.Count) |")
$statsLines.Add("| Прочитано файлов | $filesRead |")
$statsLines.Add("| Пропущено (бинарные) | $filesSkipped |")
$statsLines.Add("| Пропущено (размер) | $filesTooLarge |")
$statsLines.Add("| Общий размер | $([math]::Round($totalSize / 1MB, 2)) MB |")
$statsLines.Add("| Дата сканирования | $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') |")
$statsLines.Add("")
$statsLines.Add("**Распределение по типам файлов (топ-15):**")
$statsLines.Add("")
$statsLines.Add("| Расширение | Файлов | Размер (KB) |")
$statsLines.Add("|------------|--------|-------------|")
foreach ($stat in ($extStats | Select-Object -First 15)) {
    $statsLines.Add("| ``$($stat.Ext)`` | $($stat.Count) | $([math]::Round($stat.Size / 1KB, 1)) |")
}

# ?????????????????????????????????????????????????????????????
# Проверки окружения
# ?????????????????????????????????????????????????????????????
Write-Status "Проверка окружения..."
$envLines = [System.Collections.Generic.List[string]]::new()

# Python
try {
    $pyVer = python --version 2>&1
    $envLines.Add("### ? Python")
    $envLines.Add("``````")
    $envLines.Add($pyVer)
    $envLines.Add("``````")
} catch {
    $envLines.Add("### ? Python: не найден")
}

# pip list
try {
    $pipList = pip list --format=columns 2>&1
    $envLines.Add("### ? Python packages (pip list)")
    $envLines.Add("<details><summary>Показать полный список</summary>")
    $envLines.Add("")
    $envLines.Add("``````")
    $envLines.Add(($pipList -join "`n"))
    $envLines.Add("``````")
    $envLines.Add("</details>")
} catch {
    $envLines.Add("### ? pip list: ошибка — $($_.Exception.Message)")
}

# Docker
if (-not $SkipDockerCheck) {
    try {
        $dockerInfo = docker version --format '{{.Server.Version}} (API {{.Server.APIVersion}})' 2>&1
        $envLines.Add("")
        $envLines.Add("### ? Docker")
        $envLines.Add("**Server:** $dockerInfo")
        $envLines.Add("")
        $envLines.Add("**Запущенные контейнеры:**")
        $envLines.Add("``````")
        $dockerPs = docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}" 2>&1
        $envLines.Add(($dockerPs -join "`n"))
        $envLines.Add("``````")
    } catch {
        $envLines.Add("### ? Docker: не доступен — $($_.Exception.Message)")
    }
}

# Git
if (-not $SkipGitCheck) {
    try {
        Push-Location $ProjectPath
        $gitStatus = git status --short 2>&1
        $gitBranch = git rev-parse --abbrev-ref HEAD 2>&1
        $gitCommit = git log -1 --format="%h %s (%an, %ar)" 2>&1
        Pop-Location

        $envLines.Add("")
        $envLines.Add("### ? Git")
        $envLines.Add("**Ветка:** ``$gitBranch``")
        $envLines.Add("**Последний коммит:** $gitCommit")
        $envLines.Add("")
        $envLines.Add("**Изменения:**")
        $envLines.Add("``````")
        if ($gitStatus) { $envLines.Add(($gitStatus -join "`n")) } else { $envLines.Add("(чистая рабочая директория)") }
        $envLines.Add("``````")
    } catch {
        Pop-Location -ErrorAction SilentlyContinue
        $envLines.Add("### ? Git: не является репозиторием или git не найден")
    }
}

# ?????????????????????????????????????????????????????????????
# Сборка итогового отчёта
# ?????????????????????????????????????????????????????????????
Write-Status "Формирование отчёта..."
$report = [System.Collections.Generic.List[string]]::new()

$report.Add("# ? Project Scan Report")
$report.Add("**Проект:** ``$ProjectPath``  ")
$report.Add("**Дата:** $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  ")
$report.Add("**Сканировал:** $env:USERNAME@$env:COMPUTERNAME")
$report.Add("")
$report.Add("---")
$report.Add("")
$report.Add("## 1. Статистика")
$report.Add("")
$report.AddRange($statsLines)
$report.Add("")
$report.Add("---")
$report.Add("")
$report.Add("## 2. Структура проекта")
$report.Add("")
$report.AddRange($treeLines)
$report.Add("")
$report.Add("---")
$report.Add("")
$report.Add("## 3. Содержимое файлов")
$report.Add("")
$report.AddRange($fileContents)
$report.Add("---")
$report.Add("")
$report.Add("## 4. Окружение")
$report.Add("")
$report.AddRange($envLines)
$report.Add("")
$report.Add("---")
$report.Add("")
$report.Add("*Отчёт сгенерирован автоматически скриптом ``scan-project.ps1``*")

# Запись с UTF-8 без BOM
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllLines($reportPath, $report.ToArray(), $utf8NoBom)

# ?????????????????????????????????????????????????????????????
# Итог
# ?????????????????????????????????????????????????????????????
Write-Status "? Сканирование завершено!" "Green"
Write-Host ""
Write-Host "? Каталог: " -NoNewline -ForegroundColor DarkGray
Write-Host $ProjectPath
Write-Host "? Отчёт:   " -NoNewline -ForegroundColor DarkGray
Write-Host $reportPath -ForegroundColor Yellow
Write-Host "? Размер:  " -NoNewline -ForegroundColor DarkGray
$reportSize = (Get-Item $reportPath).Length
Write-Host "$([math]::Round($reportSize / 1KB, 1)) KB ($($report.Count) строк)"
Write-Host ""
Write-Host "Статистика:" -ForegroundColor Cyan
Write-Host "  • Каталоги:        $($directories.Count)"
Write-Host "  • Файлы:           $($files.Count)"
Write-Host "  • Прочитано:       $filesRead"
Write-Host "  • Пропущено:       $($filesSkipped + $filesTooLarge)"
Write-Host "  • Общий размер:    $([math]::Round($totalSize / 1MB, 2)) MB"