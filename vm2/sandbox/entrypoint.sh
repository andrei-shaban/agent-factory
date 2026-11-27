#!/bin/sh
# ==============================================================================
# vm2/sandbox/entrypoint.sh
# entrypoint.sh — Точка входа для песочниц gVisor (ВМ №2)
# Production Blueprint v5.3.1
# ==============================================================================

set -e

export TMPDIR=/tmp
export PYTHONDONTWRITEBYTECODE=1

# ==============================================================================
# E8: Установка динамических зависимостей из wheel-cache
# ==============================================================================
# Если в образе есть requirements-runtime.txt, устанавливаем зависимости
# на tmpfs /app/vendor перед запуском инструмента.
if [ -f /app/tools/requirements-runtime.txt ]; then
    echo "Installing runtime dependencies from wheel-cache..."
    pip install \
        --no-index \
        --find-links=/app/wheel-cache \
        --target=/tmp/vendor \
        --require-hashes \
        -r /app/tools/requirements-runtime.txt

    export PYTHONPATH=/tmp/vendor
fi

# Передача управления целевой команде
exec "$@"