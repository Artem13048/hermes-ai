"""
Экспорт данных из БД в JSON.

Все функции делегируют в `admin_service`, где реализована
единая логика импорта/экспорта.

Экспортируются ВСЕ поля, включая:
- photo_url
- long_description
- opening_hours
- benefits
- rating
- ticket_url / source_url
"""

import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

from services.admin_service import (
    export_db_to_json as _admin_export_all,
    export_city_to_json as _admin_export_city,
    save_json_file,
)


# НАСТРОЙКА

BASE_DIR = Path(__file__).resolve().parent.parent
EXPORT_DIR = BASE_DIR / "storage" / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _generate_filename(prefix: str) -> str:
    """Генерирует уникальное имя файла экспорта."""
    file_id = uuid.uuid4().hex[:8]
    date_str = datetime.now().strftime("%d-%m-%Y_%H-%M")
    return f"{prefix}_{date_str}_{file_id}.json"


def _cleanup_old_exports(days: int = 7) -> None:
    """Удаляет старые экспорты (старше N дней)."""
    try:
        now = time.time()
        for old_file in EXPORT_DIR.glob("*.json"):
            if now - old_file.stat().st_mtime > days * 86400:
                old_file.unlink()
                logging.info(f"Удалён старый экспорт: {old_file.name}")
    except Exception:
        logging.exception("Не удалось очистить старые экспорты")



# ПУБЛИЧНЫЕ ФУНКЦИИ — возвращают Path
# bot.py ожидает, что export_db_to_json() и export_city_to_json()
# возвращают ПУТЬ к файлу. Оставляем такое поведение.

def export_db_to_json() -> Path:
    """
    Выгружает ВСЮ БД в JSON-файл.
    Возвращает путь к файлу.
    """
    data = _admin_export_all()

    filename = _generate_filename("export_all")
    path = EXPORT_DIR / filename

    save_json_file(data, path)

    logging.info(
        f"Экспорт всей БД: {path} "
        f"(городов: {len(data.get('cities', []))}, "
        f"мест: {len(data.get('places', []))})"
    )

    _cleanup_old_exports()
    return path


def export_city_to_json(city_id: int) -> Path:
    """
    Выгружает ОДИН город и все его места в JSON-файл.
    Возвращает путь к файлу.
    """
    data = _admin_export_city(city_id)

    filename = _generate_filename(f"city_{city_id}")
    path = EXPORT_DIR / filename

    save_json_file(data, path)

    logging.info(
        f"Экспорт города id={city_id}: {path} "
        f"(мест: {len(data.get('places', []))})"
    )

    _cleanup_old_exports()
    return path