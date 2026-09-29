"""
Импорт и обновление городов и мест из JSON в PostgreSQL.

1. Города добавляются, если их ещё нет.
2. Фиксированные категории ищутся по имени.
3. Новое место добавляется.
4. Если место уже существует в этом городе (city_id + name),
   его данные обновляются.
5. Если в JSON поле явно указано как null, существующее значение
   также будет заменено на NULL.
6. Поля, которых вообще нет в JSON, у существующей записи не меняются.
7. Поле is_free не используется, так как его нет в таблице places.
   Бесплатное место определяется по price == "0".
"""

import json
import logging
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



# ЧТЕНИЕ JSON


def load_json_file(path: str | Path) -> dict:
    """Читает JSON-файл с диска."""

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"JSON-файл не найден: {path}"
        )

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(
            "Корень JSON должен быть объектом."
        )

    return data



# ГОРОДА


def _get_or_create_cities(
    session,
    cities_data: list
) -> dict:
    """
    Находит существующие города или создаёт отсутствующие.
    """

    city_ids = {}

    for city_data in cities_data:
        json_id = city_data.get("id")
        name = city_data.get("name")
        region = city_data.get("region")

        if not name:
            logging.warning(
                "Пропущен город без name: %s",
                city_data
            )
            continue

        city = session.scalar(
            select(City).where(
                City.name == name,
                City.region == region,
            )
        )

        if city:
            city_ids[json_id] = city.id

            logging.info(
                "Город уже существует: %s (DB id=%s)",
                name,
                city.id,
            )
            continue

        city = City(
            name=name,
            region=region,
            country=city_data.get(
                "country",
                "Россия"
            ),
            latitude=city_data.get("latitude"),
            longitude=city_data.get("longitude"),
        )

        session.add(city)
        session.flush()

        city_ids[json_id] = city.id

        logging.info(
            "Добавлен новый город: %s (DB id=%s)",
            name,
            city.id,
        )

    return city_ids



# КАТЕГОРИИ


def _get_fixed_categories(session) -> dict:
    """
    Находит фиксированные категории в БД.
    Категории не создаются автоматически.
    Если какой-то категории нет в БД, импорт завершит эту
    запись с ошибкой, а не создаст новую категорию.
    """

    category_ids = {}

    for json_id, category_name in FIXED_CATEGORIES.items():
        category = session.scalar(
            select(Category).where(
                Category.name == category_name
            )
        )

        if category is None:
            raise ValueError(
                f"В БД отсутствует фиксированная категория "
                f"'{category_name}'. "
                f"Создай её в таблице categories."
            )

        category_ids[json_id] = category.id

        logging.info(
            "Категория: %s -> DB id=%s",
            category_name,
            category.id,
        )

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

def _update_place(
    existing: Place,
    place_data: dict,
    category_id
):
    """
    Обновляет существующее место.

    Если поле присутствует в JSON, его значение записывается
    даже если оно равно None.

    Если поля нет в JSON вообще, старое значение сохраняется.
    """

    if category_id is not None:
        existing.category_id = category_id

    for field in PLACE_FIELDS:
        if field == "category_id":
            continue

        if field in place_data:
            setattr(
                existing,
                field,
                place_data[field]
            )


# СОЗДАНИЕ PLACE

def _create_place(
    place_data: dict,
    city_id: int,
    category_id: int
) -> Place:
    """
    Создаёт новый объект Place из JSON.
    """

    return Place(
        city_id=city_id,
        category_id=category_id,
        name=place_data["name"],
        address=place_data.get("address"),
        description=place_data.get("description"),
        long_description=place_data.get("long_description"),
        price=place_data.get("price", "0"),
        ticket_url=place_data.get("ticket_url"),
        source_url=place_data.get("source_url"),
        photo_url=place_data.get("photo_url"),
        latitude=place_data.get("latitude"),
        longitude=place_data.get("longitude"),
        rating=place_data.get("rating"),
        is_active=place_data.get("is_active", True),
        opening_hours=place_data.get("opening_hours"),
        benefits=place_data.get("benefits"),
    )


# МЕСТА

def _add_or_update_places(
    session,
    places_data: list,
    city_ids: dict,
    category_ids: dict,
) -> tuple[int, int, int, list[str]]:
    """
    Добавляет новые места и обновляет существующие.
    Возвращает:
        added, updated, skipped, errors
    """

    added = 0
    updated = 0
    skipped = 0
    errors = []

    for index, place_data in enumerate(
        places_data,
        start=1
    ):
        name = place_data.get(
            "name",
            "?"
        )

        # Проверка обязательного name

        if not place_data.get("name"):
            message = (
                f"Место #{index}: отсутствует поле name"
            )

            errors.append(message)
            logging.error(message)

            skipped += 1
            continue


        # Город

        json_city_id = place_data.get(
            "city_id"
        )

        if json_city_id not in city_ids:
            message = (
                f"{name}: city_id={json_city_id} "
                f"не найден среди импортируемых городов"
            )

            errors.append(message)
            logging.error(message)

            skipped += 1
            continue

        city_id = city_ids[json_city_id]

        # Категория

        json_category_id = place_data.get(
            "category_id"
        )

        if json_category_id is None:
            message = (
                f"{name}: отсутствует category_id"
            )

            errors.append(message)
            logging.error(message)

            skipped += 1
            continue

        category_id = category_ids.get(
            json_category_id
        )

        if category_id is None:
            message = (
                f"{name}: category_id={json_category_id} "
                f"не соответствует фиксированной категории"
            )

            errors.append(message)
            logging.error(message)

            skipped += 1
            continue

        # Поиск существующего места

        existing = session.scalar(
            select(Place).where(
                Place.city_id == city_id,
                Place.name == name,
            )
        )

        # Обновление существующего места

        if existing:
            _update_place(
                existing,
                place_data,
                category_id,
            )

            updated += 1

            logging.info(
                "Обновлено место: %s (DB id=%s)",
                name,
                existing.id,
            )

            continue

        # Добавление нового места

        place = _create_place(
            place_data,
            city_id,
            category_id,
        )

        session.add(place)
        session.flush()

        added += 1

        logging.info(
            "Добавлено место: %s (DB id=%s)",
            name,
            place.id,
        )

    return (
        added,
        updated,
        skipped,
        errors,
    )


# ОСНОВНОЙ ИМПОРТ

def import_from_json(data: dict) -> dict:
    """
    Импортирует JSON-структуру:
    {
        "cities": [...],
        "categories": [...],
        "places": [...]
    }
    Категории считаются фиксированными и берутся из
    FIXED_CATEGORIES.
    """

    if not isinstance(data, dict):
        return {
            "cities_added": 0,
            "categories_added": 0,
            "places_added": 0,
            "places_updated": 0,
            "places_skipped": 0,
            "errors": [
                "JSON должен быть объектом"
            ],
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
        # Количество городов до импорта

        cities_before = session.query(
            City
        ).count()

        # Города

        city_ids = _get_or_create_cities(
            session,
            data.get("cities", []),
        )

        # Фиксированные категории

        category_ids = _get_fixed_categories(
            session
        )

        # Места

        (
            places_added,
            places_updated,
            places_skipped,
            errors,
        ) = _add_or_update_places(
            session=session,
            places_data=data.get(
                "places",
                []
            ),
            city_ids=city_ids,
            category_ids=category_ids,
        )

        # Сохранение

        session.commit()

        cities_after = session.query(
            City
        ).count()

        report["cities_added"] = (
            cities_after - cities_before
        )

        report["places_added"] = places_added
        report["places_updated"] = places_updated
        report["places_skipped"] = places_skipped
        report["errors"] = errors

        logging.info(
            "Импорт завершён: "
            "добавлено мест=%s, обновлено=%s, "
            "пропущено=%s, ошибок=%s",
            places_added,
            places_updated,
            places_skipped,
            len(errors),
        )

    except Exception as exc:
        session.rollback()

        logging.exception(
            "Ошибка импорта JSON"
        )

        report["errors"].append(
            f"Общая ошибка: {exc}"
        )

    finally:
        session.close()

    return report


# ИМПОРТ ИЗ ФАЙЛА

def import_from_json_file(
    path: str | Path
) -> dict:
    """
    Читает JSON-файл и импортирует его в БД.
    """

    data = load_json_file(path)

    return import_from_json(data)


# ЗАПУСК ИЗ КОМАНДНОЙ СТРОКИ

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print(
            "Использование:\n"
            "python load_places_fixed.py <путь_к_json>"
        )
        raise SystemExit(1)

    json_path = sys.argv[1]

    result = import_from_json_file(
        json_path
    )

    print("\n" + "=" * 60)
    print("РЕЗУЛЬТАТ ИМПОРТА")
    print("=" * 60)

    print(
        f"Добавлено городов: "
        f"{result['cities_added']}"
    )

    print(
        f"Добавлено мест: "
        f"{result['places_added']}"
    )

    print(
        f"Обновлено мест: "
        f"{result['places_updated']}"
    )

    print(
        f"Пропущено мест: "
        f"{result['places_skipped']}"
    )

    print(
        f"Ошибок: "
        f"{len(result['errors'])}"
    )

    if result["errors"]:
        print("\nОшибки:")

        for error in result["errors"]:
            print(
                f"  - {error}"
            )

    print("=" * 60)