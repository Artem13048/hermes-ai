"""
Сервис генерации карты маршрута через Яндекс.Карты (Static API).
"""

import logging
import os
from pathlib import Path
from urllib.parse import urlencode

import requests

# ============================================================
# НАСТРОЙКА
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
MAPS_DIR = BASE_DIR / "storage" / "maps"
MAPS_DIR.mkdir(parents=True, exist_ok=True)

YANDEX_MAPS_API_KEY = os.getenv("YANDEX_MAPS_API_KEY", "")

STATIC_MAPS_URL = "https://static-maps.yandex.ru/1.x/"


# ============================================================
# ГЕНЕРАЦИЯ КАРТЫ
# ============================================================

def generate_route_map(route_data: list, route_id: str = "route") -> Path | None:
    """
    Генерирует статичную карту маршрута через Яндекс.Карты.

    route_data — список словарей вида:
    [
        {"name": "...", "latitude": 55.79, "longitude": 49.10},
        ...
    ]

    Возвращает путь к PNG или None, если не удалось.
    """
    if not YANDEX_MAPS_API_KEY:
        logging.warning("YANDEX_MAPS_API_KEY не задан — карта не будет создана")
        return None

    # Фильтруем места без координат
    points = [
        p for p in route_data
        if p.get("latitude") is not None
        and p.get("longitude") is not None
    ]

    if len(points) < 2:
        logging.warning(
            f"Слишком мало точек для карты: {len(points)}"
        )
        return None

    # Формируем pt-параметр: точки с номерами
    # Формат: lon,lat,style — обрати внимание на порядок!
    pt_parts = []
    for index, point in enumerate(points, start=1):
        lat = point["latitude"]
        lon = point["longitude"]

        # Стиль метки: pm2rdmN — красная метка с номером N
        # N от 1 до 99, потом — другие стили
        if index <= 99:
            style = f"pm2rdm{index}"
        else:
            style = "pm2rdm"

        # ВАЖНО: у Яндекс.Карт порядок lon,lat
        pt_parts.append(f"{lon},{lat},{style}")

    pt_param = "~".join(pt_parts)

    # Параметры запроса
    params = {
        "l": "map",         # тип карты
        "pt": pt_param,     # точки
        "size": "650,450",  # размер (максимум 650x450)
        "apikey": YANDEX_MAPS_API_KEY,
        "lang": "ru_RU",
    }

    # Собираем URL
    url = f"{STATIC_MAPS_URL}?{urlencode(params, safe='~,')}"

    # Скачиваем
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()

        path = MAPS_DIR / f"{route_id}.png"
        with open(path, "wb") as f:
            f.write(response.content)

        logging.info(f"Карта маршрута сохранена: {path}")
        return path

    except Exception:
        logging.exception("Ошибка при генерации карты")
        return None


# ============================================================
# ССЫЛКА НА ЯНДЕКС.КАРТЫ С ТОЧКАМИ МАРШРУТА
# ============================================================

def generate_yandex_maps_link(route_data: list) -> str | None:
    """
    Генерирует ссылку на Яндекс.Карты с ТОЧКАМИ маршрута
    (без построения маршрута по дорогам).

    Формат: https://yandex.ru/maps/?pt=lon,lat~lon,lat~...&z=14&l=map

    ВАЖНО: в параметре pt сначала идёт ДОЛГОТА, потом ШИРОТА.
    """
    points = []
    for place in route_data:
        lat = place.get("latitude")
        lon = place.get("longitude")

        if lat is None or lon is None:
            continue

        # Яндекс требует lon,lat (долгота, широта)
        points.append(f"{lon},{lat}")

    if len(points) < 2:
        return None

    # Точки через ~
    pt_param = "~".join(points)

    # zoom=14 — оптимально для города
    url = (
        f"https://yandex.ru/maps/"
        f"?pt={pt_param}"
        f"&z=14"
        f"&l=map"
    )
    return url