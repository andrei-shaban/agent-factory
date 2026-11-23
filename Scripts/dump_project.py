import os
from pathlib import Path

# Настройки путей
TARGET_DIR = Path(r"F:\agent-factory\vm1")
OUTPUT_FILE = Path("project_dump.txt")

# Папки и расширения файлов, которые лучше пропустить (бинарники, кэш, зависимости)
IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "env", ".idea", ".vscode", "dist", "build", ".next", ".diag", "certs"
}
IGNORE_EXTENSIONS = {
    ".exe", ".dll", ".so", ".dylib", ".bin", ".obj", ".o",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".mp4", ".mp3", ".wav", ".pdf", ".zip", ".tar", ".gz",
    ".7z", ".rar", ".pyc", ".pyo", ".sqlite", ".db", ".key",
    ".crt", ".ps1", ".txt", ".pfx"
}


def generate_tree(dir_path: Path, prefix: str = "") -> list[str]:
    """Генерирует визуальное дерево папок и файлов."""
    tree_lines = []
    try:
        items = sorted(
            [p for p in dir_path.iterdir() if p.name not in IGNORE_DIRS],
            key=lambda p: (p.is_file(), p.name.lower())
        )
    except PermissionError:
        return [f"{prefix}└── [Доступ запрещен]"]

    for index, item in enumerate(items):
        is_last = index == len(items) - 1
        connector = "└── " if is_last else "├── "
        tree_lines.append(f"{prefix}{connector}{item.name}")

        if item.is_dir():
            extension = "    " if is_last else "│   "
            tree_lines.extend(generate_tree(item, prefix + extension))

    return tree_lines


def dump_project():
    if not TARGET_DIR.exists():
        print(f"Ошибка: Путь {TARGET_DIR} не найден.")
        return

    print("Сбор структуры и содержимого...")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        # 1. Записываем дерево каталогов
        out.write(f"=== СТРУКТУРА ПРОЕКТА: {TARGET_DIR} ===\n\n")
        out.write(f"{TARGET_DIR.name}/\n")
        tree = generate_tree(TARGET_DIR)
        out.write("\n".join(tree))
        out.write("\n\n" + "=" * 60 + "\n\n")

        # 2. Записываем содержимое файлов
        out.write("=== СОДЕРЖИМОЕ ФАЙЛОВ ===\n\n")

        for root, dirs, files in os.walk(TARGET_DIR):
            # Исключаем ненужные папки из обхода
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

            for file in sorted(files):
                file_path = Path(root) / file

                # Пропускаем сам выходной файл и бинарные расширения
                if file_path.resolve() == OUTPUT_FILE.resolve():
                    continue
                if file_path.suffix.lower() in IGNORE_EXTENSIONS:
                    continue

                rel_path = file_path.relative_to(TARGET_DIR)
                out.write(f"\n--- ФАЙЛ: {rel_path} ---\n")

                try:
                    content = file_path.read_text(encoding="utf-8")
                    out.write(content)
                except UnicodeDecodeError:
                    # Пробуем кодировку Windows, если файл не в UTF-8
                    try:
                        content = file_path.read_text(encoding="cp1251")
                        out.write(content)
                    except Exception:
                        out.write("[Бинарный файл или неизвестная кодировка — пропущено]")
                except Exception as e:
                    out.write(f"[Ошибка чтения файла: {e}]")

                out.write("\n" + "-" * 40 + "\n")

    print(f"Готово! Результат сохранен в файл: {OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    dump_project()
