"""
Импорт и экспорт городов и мест из JSON в PostgreSQL.

ИМПОРТ:
1. Города добавляются, если их ещё нет (поиск по name + region).
2. Фиксированные категории ищутся по имени (1–6).
   Если их нет — создаются автоматически (create_categories=True).
3. Новое место добавляется.
4. Если место уже существует (city_id + name) — данные обновляются.
5. Пустой photo_url НЕ затирает существующее фото.
6. Поля, которых нет в JSON, у существующей записи не меняются.

ЭКСПОРТ:
- Города и места выгружаются в JSON, совместимый с импортом.
- Каждое место содержит все поля, включая photo_url, opening_hours, benefits.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import City, Category, Place


# ЛОГИРОВАНИЕ

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)



# ФИКСИРОВАННЫЕ КАТЕГОРИИ

FIXED_CATEGORIES = {
    1: "Достопримечательности",
    2: "Музеи",
    3: "Парки и прогулки",
    4: "Гастрономия",
    5: "Развлечения и активный отдых",
    6: "Религиозные объекты",
}


# ДОПУСТИМЫЕ ХОСТЫ ДЛЯ ФОТО

ALLOWED_PHOTO_HOSTS = (
    "upload.wikimedia.org",
    "commons.wikimedia.org",
    "static.tildacdn.com",
    "api.mgomz.ru",
    "cdn.restgeo.com",
    "avatars.mds.yandex.net",
    "cdn-images.mn.ru",
    "img02.rl0.ru",
    "i.pinimg.com",
    "cdn.blog.mamado.su",
    "image.eatout.ru",
    "kublog.ru",
    "tourism.krd.ru",
    "tourism.restexpert.com",
    "sdelanounas.ru",
    "topgid.net",
    "kulturologia.ru",
    "muzei-mira.com",
    "kub-inform.ru",
    "photos.wikimapia.org",
    "rus.team",
    "st7.styapokupayu.ru",
    "aif-s3.aif.ru",
    "turistplaces.ru",
    "travel4us.ru",
    "tripandme.ru",
    "ngkub.ru",
    "krasivye-mesta.ru",
    "tvkrasnodar.ru",
    "kartin.papik.pro",
    "tse1.mm.bing.net",
)

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _is_valid_photo_url(url) -> bool:
    """Проверяет, что URL похож на прямую ссылку на изображение."""
    if not url or not isinstance(url, str):
        return False

    url = url.strip()
    url_lower = url.lower()

    if not url_lower.startswith(("http://", "https://")):
        return False

    for ext in IMAGE_EXTENSIONS:
        if url_lower.endswith(ext):
            return True
        if f"{ext}?" in url_lower or f"{ext}&" in url_lower:
            return True

    for host in ALLOWED_PHOTO_HOSTS:
        if host in url_lower:
            return True

    return False



# ЧТЕНИЕ / ЗАПИСЬ JSON

def load_json_file(path: str | Path) -> dict:
    """Читает JSON-файл с диска."""
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"JSON-файл не найден: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError("Корень JSON должен быть объектом.")

    return data


def save_json_file(data: dict, path: str | Path) -> Path:
    """Сохраняет данные в JSON-файл."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    return path


# ГОРОДА

def _get_or_create_cities(session, cities_data: list) -> dict:
    """
    Находит существующие города или создаёт отсутствующие.
    Возвращает {json_city_id: db_city_id}.
    """
    city_ids = {}

    for city_data in cities_data:
        json_id = city_data.get("id")
        name = city_data.get("name")
        region = city_data.get("region")

        if not name:
            logging.warning("Пропущен город без name: %s", city_data)
            continue

        city = session.scalar(
            select(City).where(
                City.name == name,
                City.region == region,
            )
        )

        if city:
            city_ids[json_id] = city.id
            logging.info("Город уже существует: %s (DB id=%s)", name, city.id)
            continue

        city = City(
            name=name,
            region=region,
            country=city_data.get("country", "Россия"),
            latitude=city_data.get("latitude"),
            longitude=city_data.get("longitude"),
        )

        session.add(city)
        session.flush()

        city_ids[json_id] = city.id
        logging.info("Добавлен новый город: %s (DB id=%s)", name, city.id)

    return city_ids


# КАТЕГОРИИ

def _get_fixed_categories(session, create_missing: bool = True) -> dict:
    """
    Находит фиксированные категории в БД.
    Если create_missing=True — создаёт отсутствующие.
    """
    category_ids = {}

    for json_id, category_name in FIXED_CATEGORIES.items():
        category = session.scalar(
            select(Category).where(Category.name == category_name)
        )

        if category is None:
            if not create_missing:
                raise ValueError(
                    f"В БД отсутствует фиксированная категория "
                    f"'{category_name}'. Создай её в таблице categories."
                )

            category = Category(id=json_id, name=category_name)
            session.add(category)
            session.flush()
            logging.info("Создана категория: %s (id=%s)", category_name, category.id)

        category_ids[json_id] = category.id

    return category_ids


# ПОЛЯ PLACE

PLACE_FIELDS = (
    "category_id",
    "address",
    "description",
    "long_description",
    "price",
    "ticket_url",
    "source_url",
    "photo_url",
    "latitude",
    "longitude",
    "rating",
    "is_active",
    "opening_hours",
    "benefits",
)

# ОБНОВЛЕНИЕ PLACE

def _update_place(existing: Place, place_data: dict, category_id):
    """
    Обновляет существующее место.
    - photo_url пустой → не затираем старое фото.
    - Остальные поля перезаписываем, если они есть в JSON.
    """
    if category_id is not None:
        existing.category_id = category_id

    for field in PLACE_FIELDS:
        if field == "category_id":
            continue
        if field not in place_data:
            continue

        new_value = place_data[field]

        # Не затираем существующее фото пустым
        if field == "photo_url" and not new_value:
            continue

        setattr(existing, field, new_value)


# СОЗДАНИЕ PLACE

def _create_place(place_data: dict, city_id: int, category_id: int) -> Place:
    """Создаёт новый объект Place из JSON."""
    photo_url = place_data.get("photo_url")

    if photo_url and not _is_valid_photo_url(photo_url):
        logging.warning(f"  ⚠️ {place_data['name']}: подозрительный photo_url")
        photo_url = None

    place = Place(
        city_id=city_id,
        category_id=category_id,
        name=place_data["name"],
        address=place_data.get("address"),
        description=place_data.get("description"),
        long_description=place_data.get("long_description"),
        price=place_data.get("price", "0"),
        ticket_url=place_data.get("ticket_url"),
        source_url=place_data.get("source_url"),
        photo_url=photo_url,
        latitude=place_data.get("latitude"),
        longitude=place_data.get("longitude"),
        rating=place_data.get("rating"),
        is_active=place_data.get("is_active", True),
        opening_hours=place_data.get("opening_hours"),
        benefits=place_data.get("benefits"),
    )

    if photo_url:
        logging.info(f"  📷 {place_data['name']}: фото загружено")
    else:
        logging.warning(f"  ⚠️ {place_data['name']}: фото отсутствует")

    return place


# МЕСТА

def _add_or_update_places(
    session,
    places_data: list,
    city_ids: dict,
    category_ids: dict,
) -> tuple[int, int, int, list[str]]:
    """
    Добавляет новые места и обновляет существующие.
    Возвращает: added, updated, skipped, errors.
    """
    added = 0
    updated = 0
    skipped = 0
    errors = []

    for index, place_data in enumerate(places_data, start=1):
        name = place_data.get("name", "?")

        if not place_data.get("name"):
            message = f"Место #{index}: отсутствует поле name"
            errors.append(message)
            logging.error(message)
            skipped += 1
            continue

        json_city_id = place_data.get("city_id")

        if json_city_id not in city_ids:
            message = f"{name}: city_id={json_city_id} не найден"
            errors.append(message)
            logging.error(message)
            skipped += 1
            continue

        city_id = city_ids[json_city_id]

        json_category_id = place_data.get("category_id")

        if json_category_id is None:
            message = f"{name}: отсутствует category_id"
            errors.append(message)
            logging.error(message)
            skipped += 1
            continue

        category_id = category_ids.get(json_category_id)

        if category_id is None:
            message = (
                f"{name}: category_id={json_category_id} "
                f"не соответствует фиксированной категории"
            )
            errors.append(message)
            logging.error(message)
            skipped += 1
            continue

        existing = session.scalar(
            select(Place).where(
                Place.city_id == city_id,
                Place.name == name,
            )
        )

        if existing:
            _update_place(existing, place_data, category_id)
            updated += 1
            continue

        place = _create_place(place_data, city_id, category_id)
        session.add(place)
        session.flush()
        added += 1

    return (added, updated, skipped, errors)


# ОСНОВНОЙ ИМПОРТ

def import_from_json(data: dict, create_categories: bool = True) -> dict:
    """
    Импортирует JSON-структуру:
    {
        "cities": [...],
        "places": [...]
    }
    """
    if not isinstance(data, dict):
        return {
            "cities_added": 0,
            "places_added": 0,
            "places_updated": 0,
            "places_skipped": 0,
            "places_with_photo": 0,
            "total_places": 0,
            "errors": ["JSON должен быть объектом"],
        }

    report = {
        "cities_added": 0,
        "places_added": 0,
        "places_updated": 0,
        "places_skipped": 0,
        "places_with_photo": 0,
        "total_places": 0,
        "errors": [],
    }

    session = SessionLocal()

    try:
        cities_before = session.query(City).count()

        city_ids = _get_or_create_cities(session, data.get("cities", []))
        category_ids = _get_fixed_categories(session, create_missing=create_categories)

        (places_added, places_updated, places_skipped, errors) = _add_or_update_places(
            session=session,
            places_data=data.get("places", []),
            city_ids=city_ids,
            category_ids=category_ids,
        )

        session.commit()

        cities_after = session.query(City).count()

        report["cities_added"] = cities_after - cities_before
        report["places_added"] = places_added
        report["places_updated"] = places_updated
        report["places_skipped"] = places_skipped
        report["errors"] = errors

        places_with_photo = session.query(Place).filter(
            Place.photo_url.isnot(None),
            Place.photo_url != "",
        ).count()
        total_places = session.query(Place).count()

        report["places_with_photo"] = places_with_photo
        report["total_places"] = total_places

        logging.info(
            f"Импорт завершён: добавлено={places_added}, "
            f"обновлено={places_updated}, пропущено={places_skipped}, "
            f"ошибок={len(errors)}"
        )
        logging.info(f"📷 Фото: {places_with_photo}/{total_places} мест")

    except Exception as exc:
        session.rollback()
        logging.exception("Ошибка импорта JSON")
        report["errors"].append(f"Общая ошибка: {exc}")

    finally:
        session.close()

    return report


def import_from_json_file(path: str | Path, create_categories: bool = True) -> dict:
    """Читает JSON-файл и импортирует его в БД."""
    data = load_json_file(path)
    return import_from_json(data, create_categories=create_categories)


# ЭКСПОРТ ВСЕЙ БД

def export_db_to_json() -> dict:
    """
    Выгружает ВСЕ города и места из БД в структуру, совместимую с импортом.
    Возвращает словарь.
    """
    session = SessionLocal()

    try:
        cities = session.query(City).order_by(City.id).all()

        city_id_map = {}
        cities_json = []

        for index, city in enumerate(cities, start=1):
            city_id_map[city.id] = index
            cities_json.append({
                "id": index,
                "name": city.name,
                "region": city.region,
                "country": city.country or "Россия",
                "latitude": city.latitude,
                "longitude": city.longitude,
            })

        places = session.query(Place).order_by(Place.city_id, Place.id).all()

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
                "long_description": place.long_description,
                "price": place.price,
                "ticket_url": place.ticket_url,
                "source_url": place.source_url,
                "photo_url": place.photo_url,
                "latitude": place.latitude,
                "longitude": place.longitude,
                "rating": place.rating,
                "is_active": place.is_active,
                "opening_hours": place.opening_hours,
                "benefits": place.benefits,
            })

        return {
            "exported_at": datetime.utcnow().isoformat(),
            "cities": cities_json,
            "places": places_json,
        }

    finally:
        session.close()


def export_db_to_file(path: str | Path) -> Path:
    """Выгружает всю БД в JSON-файл."""
    data = export_db_to_json()
    return save_json_file(data, path)


# ЭКСПОРТ ОДНОГО ГОРОДА

def export_city_to_json(city_id: int) -> dict:
    """Выгружает ОДИН город и все его места."""
    session = SessionLocal()

    try:
        city = session.query(City).filter(City.id == city_id).first()
        if not city:
            raise ValueError(f"Город id={city_id} не найден")

        cities_json = [{
            "id": 1,
            "name": city.name,
            "region": city.region,
            "country": city.country or "Россия",
            "latitude": city.latitude,
            "longitude": city.longitude,
        }]

        places = session.query(Place).filter(
            Place.city_id == city_id
        ).order_by(Place.id).all()

        places_json = []
        for place in places:
            places_json.append({
                "city_id": 1,
                "category_id": place.category_id,
                "name": place.name,
                "address": place.address,
                "description": place.description,
                "long_description": place.long_description,
                "price": place.price,
                "ticket_url": place.ticket_url,
                "source_url": place.source_url,
                "photo_url": place.photo_url,
                "latitude": place.latitude,
                "longitude": place.longitude,
                "rating": place.rating,
                "is_active": place.is_active,
                "opening_hours": place.opening_hours,
                "benefits": place.benefits,
            })

        return {
            "exported_at": datetime.utcnow().isoformat(),
            "cities": cities_json,
            "places": places_json,
        }

    finally:
        session.close()


# ЗАПУСК ИЗ КОМАНДНОЙ СТРОКИ

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Использование:")
        print("  python admin_service.py import <путь_к_json>")
        print("  python admin_service.py export-all <путь_к_json>")
        print("  python admin_service.py export-city <city_id> <путь_к_json>")
        raise SystemExit(1)

    command = sys.argv[1]

    if command == "import":
        if len(sys.argv) != 3:
            print("Укажите путь к JSON")
            raise SystemExit(1)

        result = import_from_json_file(sys.argv[2])
        print("\n" + "=" * 60)
        print("РЕЗУЛЬТАТ ИМПОРТА")
        print("=" * 60)
        print(f"Добавлено городов: {result['cities_added']}")
        print(f"Добавлено мест: {result['places_added']}")
        print(f"Обновлено мест: {result['places_updated']}")
        print(f"Пропущено мест: {result['places_skipped']}")
        print(f"Ошибок: {len(result['errors'])}")
        print(f"📷 Мест с фото: {result['places_with_photo']}/{result['total_places']}")
        if result["errors"]:
            print("\nОшибки:")
            for error in result["errors"]:
                print(f"  - {error}")
        print("=" * 60)

    elif command == "export-all":
        if len(sys.argv) != 3:
            print("Укажите путь для сохранения")
            raise SystemExit(1)

        path = export_db_to_file(sys.argv[2])
        print(f"Экспорт сохранён: {path}")

    elif command == "export-city":
        if len(sys.argv) != 4:
            print("Укажите city_id и путь")
            raise SystemExit(1)

        city_id = int(sys.argv[2])
        data = export_city_to_json(city_id)
        path = save_json_file(data, sys.argv[3])
        print(f"Экспорт города id={city_id}: {path}")

    else:
        print(f"Неизвестная команда: {command}")
        raise SystemExit(1)