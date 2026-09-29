"""
Экспортрует данные из БД в JSON.
"""

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

from app.database import SessionLocal
from app.models import City, Place


# ============================================================
# НАСТРОЙКА
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
EXPORT_DIR = BASE_DIR / "storage" / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ
# ============================================================

def _safe_filename(text: str) -> str:
    """Превращает название города в безопасное имя файла."""
    text = text.encode("ascii", "ignore").decode() or "city"
    text = re.sub(r"[^\w\-]", "_", text)
    text = text.strip("_")
    return text or "city"


def _cleanup_old_exports(days: int = 7):
    """Удаляет старые экспорты (старше N дней)."""
    try:
        import time
        now = time.time()
        for old_file in EXPORT_DIR.glob("*.json"):
            if now - old_file.stat().st_mtime > days * 86400:
                old_file.unlink()
                logging.info(f"Удалён старый экспорт: {old_file.name}")
    except Exception:
        logging.exception("Не удалось очистить старые экспорты")


# ============================================================
# ЭКСПОРТ ВСЕЙ БД
# ============================================================

def export_db_to_json() -> Path:
    """
    Выгружает ВСЕ города и места из БД в JSON-файл.
    Возвращает путь к файлу.
    """
    session = SessionLocal()

    try:
        cities = session.query(City).order_by(City.id).all()

        # Маппинг: real_db_id -> json_id (1, 2, 3, ...)
        city_id_map = {}
        cities_json = []

        for index, city in enumerate(cities, start=1):
            city_id_map[city.id] = index
            cities_json.append({
                "id": index,
                "name": city.name,
                "region": getattr(city, "region", None),
                "country": getattr(city, "country", "Россия"),
                "latitude": city.latitude,
                "longitude": city.longitude,
            })

        # Места
        places = session.query(Place).order_by(
            Place.city_id, Place.id
        ).all()

        places_json = []
        for place in places:
            json_city_id = city_id_map.get(place.city_id)
            if json_city_id is None:
                continue

            places_json.append({
                "city_id": json_city_id,
                "category_id": place.category_id,
                "name": place.name,
                "address": place.address,
                "description": place.description,
                "price": place.price,
                "ticket_url": place.ticket_url,
                "source_url": place.source_url,
                "latitude": place.latitude,
                "longitude": place.longitude,
                "rating": place.rating,
                "is_active": place.is_active,
            })

        data = {
            "exported_at": datetime.utcnow().isoformat(),
            "cities": cities_json,
            "places": places_json,
        }

        # Имя файла
        file_id = uuid.uuid4().hex[:8]
        date_str = datetime.now().strftime("%d-%m-%Y_%H-%M")
        filename = f"export_all_{date_str}_{file_id}.json"
        path = EXPORT_DIR / filename

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logging.info(
            f"Экспорт всей БД: {path} "
            f"(городов: {len(cities_json)}, мест: {len(places_json)})"
        )

        _cleanup_old_exports()
        return path

    finally:
        session.close()


# ============================================================
# ЭКСПОРТ ОДНОГО ГОРОДА
# ============================================================

def export_city_to_json(city_id: int) -> Path:
    """
    Выгружает ОДИН город и все его места в JSON.
    Возвращает путь к файлу.
    """
    session = SessionLocal()

    try:
        city = session.query(City).filter(City.id == city_id).first()
        if not city:
            raise ValueError(f"Город id={city_id} не найден")

        # Город — всегда json_id = 1 (он один в файле)
        cities_json = [{
            "id": 1,
            "name": city.name,
            "region": getattr(city, "region", None),
            "country": getattr(city, "country", "Россия"),
            "latitude": city.latitude,
            "longitude": city.longitude,
        }]

        # Места этого города
        places = session.query(Place).filter(
            Place.city_id == city_id
        ).order_by(Place.id).all()

        places_json = []
        for place in places:
            places_json.append({
                "city_id": 1,            # всегда 1
                "category_id": place.category_id,
                "name": place.name,
                "address": place.address,
                "description": place.description,
                "price": place.price,
                "ticket_url": place.ticket_url,
                "source_url": place.source_url,
                "latitude": place.latitude,
                "longitude": place.longitude,
                "rating": place.rating,
                "is_active": place.is_active,
            })

        data = {
            "exported_at": datetime.utcnow().isoformat(),
            "cities": cities_json,
            "places": places_json,
        }

        safe_name = _safe_filename(city.name)
        file_id = uuid.uuid4().hex[:6]
        date_str = datetime.now().strftime("%d-%m-%Y")
        filename = f"city_{safe_name}_{date_str}_{file_id}.json"
        path = EXPORT_DIR / filename

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logging.info(
            f"Экспорт города: {city.name} "
            f"(мест: {len(places_json)}) → {path}"
        )

        _cleanup_old_exports()
        return path

    finally:
        session.close()