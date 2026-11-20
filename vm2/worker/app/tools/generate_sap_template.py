"""
Generate SAP Template — Инструмент для песочницы gVisor.
Production Blueprint v5.1

Назначение:
  - Генерация Excel-шаблона SAP (форма Z_GFL_REGFILSRV) из позиций акта.
  - Запускается в изолированной песочнице toolset-excel.
  - Принимает positions.json и meta.json через /app/input.
  - Возвращает output.xlsx через /app/output.

Формат входных данных:
  positions.json: [{"number": 1, "name": "...", "unit": "...", "quantity": 10, "price": 100, "amount": 1000}, ...]
  meta.json: {"run_id": "...", "contract_id": "...", "document_type": "ks2"}

Формат выходных данных:
  output.xlsx: Excel-файл с заполненным шаблоном SAP
"""

import json
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill


def load_input():
    """Загрузка входных данных из /app/input."""
    input_dir = Path("/app/input")

    with open(input_dir / "positions.json", "r", encoding="utf-8") as f:
        positions = json.load(f)

    with open(input_dir / "meta.json", "r", encoding="utf-8") as f:
        meta = json.load(f)

    return positions, meta


def create_sap_template(positions, meta):
    """
    Создание Excel-шаблона SAP (форма Z_GFL_REGFILSRV).

    Структура:
      - Шапка: номер акта, дата, договор, подрядчик
      - Таблица позиций: №, Наименование, Ед.изм., Количество, Цена, Сумма
      - Итого: общая сумма
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Акт выполненных работ"

    # Стили
    header_font = Font(name="Arial", size=12, bold=True)
    cell_font = Font(name="Arial", size=10)
    header_fill = PatternFill(start_color="D3D3D3", end_color="D3D3D3", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    center_alignment = Alignment(horizontal="center", vertical="center")
    left_alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # Шапка документа
    ws.merge_cells("A1:F1")
    ws["A1"] = f"Акт выполненных работ № {meta.get('document_type', 'UNKNOWN').upper()}"
    ws["A1"].font = Font(name="Arial", size=14, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells("A2:F2")
    ws["A2"] = f"Договор: {meta.get('contract_id', 'Не указан')}"
    ws["A2"].font = header_font
    ws["A2"].alignment = Alignment(horizontal="left", vertical="center")

    ws.merge_cells("A3:F3")
    ws["A3"] = f"Run ID: {meta.get('run_id', 'Не указан')}"
    ws["A3"].font = cell_font
    ws["A3"].alignment = Alignment(horizontal="left", vertical="center")

    # Заголовки таблицы
    headers = ["№", "Наименование работ/материалов", "Ед.изм.", "Количество", "Цена, руб.", "Сумма, руб."]
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=5, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_alignment
        cell.border = thin_border

    # Ширина колонок
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 50
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 15
    ws.column_dimensions["F"].width = 15

    # Заполнение позиций
    row = 6
    total_amount = 0

    for pos in positions:
        ws.cell(row=row, column=1, value=pos.get("number", row - 5)).font = cell_font
        ws.cell(row=row, column=1).alignment = center_alignment
        ws.cell(row=row, column=1).border = thin_border

        ws.cell(row=row, column=2, value=pos.get("name", "")).font = cell_font
        ws.cell(row=row, column=2).alignment = left_alignment
        ws.cell(row=row, column=2).border = thin_border

        ws.cell(row=row, column=3, value=pos.get("unit", "")).font = cell_font
        ws.cell(row=row, column=3).alignment = center_alignment
        ws.cell(row=row, column=3).border = thin_border

        ws.cell(row=row, column=4, value=pos.get("quantity", 0)).font = cell_font
        ws.cell(row=row, column=4).alignment = center_alignment
        ws.cell(row=row, column=4).border = thin_border
        ws.cell(row=row, column=4).number_format = "#,##0.00"

        ws.cell(row=row, column=5, value=pos.get("price", 0)).font = cell_font
        ws.cell(row=row, column=5).alignment = center_alignment
        ws.cell(row=row, column=5).border = thin_border
        ws.cell(row=row, column=5).number_format = "#,##0.00"

        amount = pos.get("amount", pos.get("quantity", 0) * pos.get("price", 0))
        ws.cell(row=row, column=6, value=amount).font = cell_font
        ws.cell(row=row, column=6).alignment = center_alignment
        ws.cell(row=row, column=6).border = thin_border
        ws.cell(row=row, column=6).number_format = "#,##0.00"

        total_amount += amount
        row += 1

    # Итого
    ws.merge_cells(f"A{row}:E{row}")
    ws.cell(row=row, column=1, value="ИТОГО:").font = Font(name="Arial", size=11, bold=True)
    ws.cell(row=row, column=1).alignment = Alignment(horizontal="right", vertical="center")
    ws.cell(row=row, column=1).border = thin_border

    ws.cell(row=row, column=6, value=total_amount).font = Font(name="Arial", size=11, bold=True)
    ws.cell(row=row, column=6).alignment = center_alignment
    ws.cell(row=row, column=6).border = thin_border
    ws.cell(row=row, column=6).number_format = "#,##0.00"

    # Подписи
    row += 2
    ws.merge_cells(f"A{row}:F{row}")
    ws.cell(row=row, column=1, value="Подпись ответственного лица: ___________________").font = cell_font
    ws.cell(row=row, column=1).alignment = Alignment(horizontal="left", vertical="center")

    return wb


def save_output(wb):
    """Сохранение Excel-файла в /app/output."""
    output_dir = Path("/app/output")
    output_dir.mkdir(exist_ok=True)

    output_path = output_dir / "output.xlsx"
    wb.save(output_path)

    print(f"Файл сохранён: {output_path}")
    return output_path


def main():
    """Точка входа для запуска в песочнице."""
    try:
        positions, meta = load_input()
        print(f"Загружено позиций: {len(positions)}")
        print(f"Метаданные: {meta}")

        wb = create_sap_template(positions, meta)
        output_path = save_output(wb)

        print(f"Генерация завершена: {output_path}")
        sys.exit(0)
    except Exception as e:
        print(f"Ошибка генерации: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()