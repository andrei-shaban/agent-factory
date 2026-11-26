# vm2/worker/app/graphs/__init__.py
"""
Graphs package. Явная регистрация графов при импорте пакета
(точка инициализации реестра). Цикла с tasks.py нет: граф импортирует
cancellation, а не tasks.
"""
from worker.app.graphs import document_processing  # noqa: F401  (self-registers)