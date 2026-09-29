"""
Импорт городов и мест из JSON в БД.
Адаптация скрипта load_places.py для вызова из бота.
"""

import json
import logging
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import City, Category, Place


def load_json_file(path: str | Path) -> dict:
    """Читает JSON-файл с диска."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"JSON-файл не найден: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _get_or_create_cities(session, cities_data):
    """Добавляет города, возвращает маппинг json_id -> db_id."""
    city_ids = {}
    for city_data in cities_data:
        city = session.scalar(
            select(City).where(
                City.name == city_data["name"],
                City.region == city_data.get("region"),
            )
        )
        if city:
            city_ids[city_data["id"]] = city.id
            continue

        city = City(
            name=city_data["name"],
            region=city_data.get("region"),
            country=city_data.get("country", "Россия"),
            latitude=city_data.get("latitude"),
            longitude=city_data.get("longitude"),
        )
        session.add(city)
        session.flush()
        city_ids[city_data["id"]] = city.id
    return city_ids


# ============================================================
# ФИКСИРОВАННЫЕ КАТЕГОРИИ
# ============================================================
# Эти id используются в template.json как category_id.
# Соответствие: category_id из JSON -> имя категории в БД.
#

FIXED_CATEGORIES = {
    1: "Достопримечательности",
    2: "Музеи",
    3: "Парки и прогулки",
    4: "Гастрономия",
    5: "Развлечения и активный отдых",
    6: "Религиозные объекты",
}


def _ensure_fixed_categories(session) -> dict:
    """
    Гарантирует, что все фиксированные категории есть в БД.
    Возвращает маппинг: json_id -> db_id.

    Если категория с таким именем уже есть — берёт её id.
    Если нет — создаёт.
    """
    category_ids = {}

    for json_id, name in FIXED_CATEGORIES.items():
        category = session.scalar(
            select(Category).where(Category.name == name)
        )
        if category:
            category_ids[json_id] = category.id
            continue

        category = Category(name=name)
        session.add(category)
        session.flush()
        category_ids[json_id] = category.id

    return category_ids


def _add_places(session, places_data, city_ids, category_ids):
    """Добавляет места. Возвращает (added, skipped, errors)."""
    added = 0
    updated = 0
    skipped = 0
    errors = []

    for place_data in places_data:
        json_city_id = place_data.get("city_id")
        json_cat_id = place_data.get("category_id")

        if json_city_id not in city_ids:
            errors.append(
                f"{place_data.get('name', '?')}: "
                f"city_id={json_city_id} не найден"
            )
            continue

        city_id = city_ids[json_city_id]

        # Категория: маппим json_id -> db_id, если есть
        category_id = None
        if json_cat_id is not None:
            category_id = category_ids.get(json_cat_id)
            if category_id is None:
                errors.append(
                    f"{place_data.get('name', '?')}: "
                    f"category_id={json_cat_id} не найден"
                )
                continue

        existing = session.scalar(
            select(Place).where(
                Place.city_id == city_id,
                Place.name == place_data["name"],
            )
        )
        if existing:
            # Обновляем существующее место (включая фото)
            existing.category_id = category_id or existing.category_id
            existing.address = place_data.get("address") or existing.address
            existing.description = place_data.get("description") or existing.description
            existing.price = place_data.get("price", existing.price)
            existing.ticket_url = place_data.get("ticket_url") or existing.ticket_url
            existing.source_url = place_data.get("source_url") or existing.source_url
            existing.photo_url = place_data.get("photo_url") or existing.photo_url
            existing.latitude = place_data.get("latitude", existing.latitude)
            existing.longitude = place_data.get("longitude", existing.longitude)
            existing.rating = place_data.get("rating", existing.rating)
            existing.is_active = place_data.get("is_active", True)

            updated += 1
            continue

        place = Place(
            city_id=city_id,
            category_id=category_id,
            name=place_data["name"],
            address=place_data.get("address"),
            description=place_data.get("description"),
            price=place_data.get("price", 0),
            ticket_url=place_data.get("ticket_url"),
            source_url=place_data.get("source_url"),
            latitude=place_data.get("latitude"),
            longitude=place_data.get("longitude"),
            rating=place_data.get("rating"),
            is_active=place_data.get("is_active", True),
            photo_url=place_data.get("photo_url"),
        )
        session.add(place)
        session.flush()
        added += 1

    return added, updated, skipped, errors

def import_from_json(data: dict) -> dict:
    """
    Импортирует данные из JSON-структуры.

    Ожидаемый формат:
    {
      "cities":     [{"id": 1, "name": "...", ...}, ...],
      "categories": [{"id": 1, "name": "..."}, ...],
      "places":     [{"city_id": 1, "category_id": 1, ...}, ...]
    }

    Возвращает отчёт:
    {
      "cities_added": int,
      "categories_added": int,
      "places_added": int,
      "places_skipped": int,
      "errors": [str, ...],
    }
    """
    if not isinstance(data, dict):
        return {
            "cities_added": 0,
            "categories_added": 0,
            "places_added": 0,
            "places_skipped": 0,
            "errors": ["JSON должен быть объектом"],
        }

    report = {
        "cities_added": 0,
        "categories_added": 0,
        "places_added": 0,
        "places_updated": 0,
        "places_skipped": 0,
        "errors": [],
    }

    session = SessionLocal()
    try:
        # Считаем до импорта, чтобы понять, сколько добавилось
        cities_before = session.query(City).count()
        cats_before = session.query(Category).count()

        city_ids = _get_or_create_cities(
            session, data.get("cities", [])
        )
        
        category_ids = _ensure_fixed_categories(session)

        added, updated, skipped, errors = _add_places(
            session,
            data.get("places", []),
            city_ids,
            category_ids,
        )

        session.commit()

        cities_after = session.query(City).count()
        cats_after = session.query(Category).count()

        report["cities_added"] = cities_after - cities_before
        report["categories_added"] = cats_after - cats_before
        report["places_added"] = added
        report["places_updated"] = updated  
        report["places_skipped"] = skipped
        report["errors"] = errors

    except Exception as e:
        session.rollback()
        logging.exception("Ошибка импорта JSON")
        report["errors"].append(f"Общая ошибка: {e}")
    finally:
        session.close()

    return report


def import_from_json_file(path: str | Path) -> dict:
    """Удобная обёртка: читает файл и импортирует."""
    data = load_json_file(path)
    return import_from_json(data)