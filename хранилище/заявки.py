"""Сохранение заявок на консультацию в JSON-файл.

Файл `заявки.json` лежит рядом с этим модулем, в папке `хранилище/`.
Там персональные данные (имя, контакт), поэтому файл не коммитится:
строка `заявки.json` есть в .gitignore проекта.
"""

import json
from datetime import datetime
from pathlib import Path

# Путь считаем от самого модуля, а не от текущей папки запуска:
# так файл найдётся, откуда бы ни запустили bot.py.
APPLICATIONS_PATH = Path(__file__).resolve().parent / "заявки.json"


def _backup_broken(file_path: Path) -> Path:
    """Переименовывает повреждённый файл, чтобы данные не пропали молча."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = file_path.with_name(f"заявки_повреждён_{stamp}.json")
    file_path.replace(backup)
    return backup


def read_applications(path: Path | None = None) -> list[dict]:
    """Читает все заявки.

    Если файла ещё нет — возвращает пустой список. Если файл повреждён
    (например, его правили руками и сломали JSON), сохраняет копию рядом
    и начинает новый список, чтобы бот не падал на каждой заявке.
    """
    file_path = Path(path) if path is not None else APPLICATIONS_PATH
    if not file_path.is_file():
        return []

    try:
        with file_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError:
        _backup_broken(file_path)
        return []

    if not isinstance(data, list):
        _backup_broken(file_path)
        return []
    return data


def save_application(
    user_id: int,
    name: str,
    time_format: str,
    contact: str,
    path: Path | None = None,
) -> dict:
    """Добавляет заявку в JSON-файл и возвращает сохранённую запись.

    Поля: user_id, name, time_format, contact, timestamp (ISO 8601
    с часовым поясом). Файл пишется с indent=4, чтобы его можно было
    открыть и прочитать глазами.

    path нужен только для тестов: по умолчанию пишем в хранилище/заявки.json.
    """
    name = name.strip()
    time_format = time_format.strip()
    contact = contact.strip()
    if not name or not contact:
        raise ValueError("Имя и контакт не могут быть пустыми")

    file_path = Path(path) if path is not None else APPLICATIONS_PATH
    applications = read_applications(file_path)

    application = {
        "user_id": user_id,
        "name": name,
        "time_format": time_format,
        "contact": contact,
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    applications.append(application)

    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(applications, file, ensure_ascii=False, indent=4)
        file.write("\n")

    return application
