# pull-vm2-latest.ps1 — pull latest images for VM-2
$ErrorActionPreference = 'Stop'

Write-Host "==> Redis" -ForegroundColor Cyan
docker pull redis:latest

Write-Host "==> PostgreSQL + pgvector" -ForegroundColor Cyan
docker pull pgvector/pgvector:latest

Write-Host "==> MongoDB" -ForegroundColor Cyan
docker pull mongo:latest

Write-Host "==> SeaweedFS" -ForegroundColor Cyan
docker pull chrislusf/seaweedfs:latest

Write-Host "==> Docker Registry" -ForegroundColor Cyan
docker pull registry:latest

Write-Host "==> Prometheus" -ForegroundColor Cyan
docker pull prom/prometheus:latest

Write-Host "==> Loki" -ForegroundColor Cyan
docker pull grafana/loki:latest

Write-Host "==> Grafana Alloy" -ForegroundColor Cyan
docker pull grafana/alloy:latest

Write-Host ""
Write-Host "==> done" -ForegroundColor Green
Write-Host ""
Write-Host "Downloaded images:" -ForegroundColor Yellow
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"

Write-Host ""
Write-Host "Now pin versions with docker tag before production use." -ForegroundColor Yellow