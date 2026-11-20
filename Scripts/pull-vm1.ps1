# pull-vm1.ps1 — загрузка образов ВМ-1
$ErrorActionPreference = 'Stop'

Write-Host "==> nginx" -ForegroundColor Cyan
docker pull nginx:stable-alpine3.24

Write-Host "==> librechat" -ForegroundColor Cyan
docker pull librechat/librechat

Write-Host "==> node (base for react-app build)" -ForegroundColor Cyan
docker pull node:27.0.0-alpha.0.1.1-trixie-slim

Write-Host "==> grafana" -ForegroundColor Cyan
docker pull --platform linux/amd64 grafana/grafana:13.3.1

Write-Host "==> grafana alloy" -ForegroundColor Cyan
docker pull grafana/alloy

Write-Host "==> keycloak" -ForegroundColor Cyan
docker pull keycloak/keycloak

Write-Host "==> oauth2-proxy" -ForegroundColor Cyan
docker pull --platform linux/amd64 quay.io/oauth2-proxy/oauth2-proxy:v7.15.6

Write-Host "`n==> done" -ForegroundColor Green
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"