"""
AG-UI Gateway — FastAPI application package.

Production Blueprint v5.2 — ВМ №1

Приём RunAgentInput от AG-UI клиентов, постановка задач в Redis фабрики (ВМ №2),
SSE-relay событий от воркеров к браузерам. Граф не исполняет — это задача Worker.
"""

__version__ = "1.0.0"
__author__ = "Factory Agents Platform"